# Phase 4 — Chat and a Provider-Neutral Model Gateway

Phase 4 makes the backend an AI workspace for the first time. The goal is not a clever prompt. The goal is a reliable boundary around an unreliable, paid network call: persist what happened, keep provider code replaceable, measure usage and latency, and test without spending money.

## Outcome, prerequisites, and non-goals

At the checkpoint, an authenticated user can manage Conversations, send a message, receive a persisted assistant response through a deterministic fake model, inspect message history and model-call metadata, and optionally switch local configuration to one real Anthropic adapter. Provider timeouts and failures leave a queryable failed call. Duplicate client retries do not duplicate the model call.

Prerequisites: completed Phases 0–3; owner-scoped repository methods; synchronous SQLAlchemy 2; PostgreSQL/Alembic; Pydantic 2; a fixed-clock testing pattern; and the common error envelope.

Non-goals: tools/function calling, memory retrieval, file or image inputs, web research, prompt management UI, automatic retries, background workers, streaming, model comparison, or safety-policy completeness. Those belong to later phases. Model output is untrusted text and cannot authorize a tool or database action.

## 1. Concepts before code

A model API is an external system: it can be slow, unavailable, rate-limited, changed, or expensive. An HTTP `200` can still contain output your application should not trust. Treat the provider boundary like a payment boundary, not like a local string function.

A **port** is the small interface your application needs. An **adapter** translates that interface to one vendor's SDK. The Chat service imports the port types, never `anthropic` types. This is dependency inversion: stable application policy points toward a small internal contract; unstable vendor details remain at the edge.

An LLM is normally nondeterministic. Even with temperature zero, providers can change implementations. Automated tests therefore use a deterministic fake. A real-provider smoke test is explicit, paid, and excluded from the normal test suite.

An **idempotency key** identifies one intended operation across client retries. If a browser loses the response and repeats a POST, the same key must return the prior outcome rather than create another paid call. The key is not authentication. Bind it to the current user and a fingerprint of conversation/body/config.

Do not hold a database transaction open while waiting on the provider. Long network waits monopolize connections and locks. Persist the intended call, commit, call the network, then open a second transaction to finalize. That creates an honest crash window: a process can die while a call remains `started`. This phase adds a manual stale-call repair command; a later durable worker will recover more intelligently.

## 2. Architecture and lifecycle

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant DB as PostgreSQL
    participant LLM as LLMClient adapter
    Client->>API: POST message + Idempotency-Key
    API->>DB: lock conversation; save user message, input snapshot, started call
    DB-->>API: commit transaction A
    API->>LLM: generate outside DB transaction
    LLM-->>API: result or classified failure
    API->>DB: save assistant message or failed call
    DB-->>API: commit transaction B
    API-->>Client: response or safe error with model_call_id
```

Only one `started` model call may exist per Conversation in Phase 4. This keeps message ordering understandable. Concurrent sends to the same Conversation return a conflict; they do not quietly build overlapping histories.

## 3. Dependencies and configuration

The fake adapter uses only the standard library, so build through the fake checkpoint before installing a vendor SDK. For the optional real adapter, add the currently verified compatible range:

```toml
"anthropic>=0.120,<1",
```

Then run `python -m pip install -e ".[dev]"` and update your lock/frozen artifact. This range reflects the documentation checked when this chapter was written; inspect the official Python SDK release and migration notes on your installation day.

Add provider-neutral settings:

```dotenv
APP_LLM_PROVIDER=fake
APP_LLM_MODEL=fake-v1
APP_LLM_TIMEOUT_SECONDS=30
APP_LLM_MAX_OUTPUT_TOKENS=512
APP_LLM_MAX_INPUT_CHARACTERS=50000
APP_LLM_SYSTEM_PROMPT=You are a concise personal workspace assistant.
APP_ANTHROPIC_API_KEY=
```

The real `.env` is ignored. `.env.example` contains only placeholders. Fail startup if `provider=anthropic` but the key or model is empty. Do not log settings wholesale: that would expose the API key and system prompt. Store a version/hash of the system prompt with a call, not a secret-bearing configuration dump.

## 4. Folder changes and rationale

```text
app/
├── core/llm/
│   ├── types.py                 # internal request/result/value types
│   ├── client.py                # LLMClient Protocol
│   ├── fake.py                  # deterministic, no-network adapter
│   └── anthropic.py             # the only module importing vendor SDK
└── modules/chat/
    ├── model.py                 # conversations, messages, model_calls
    ├── schemas.py               # HTTP contracts, never SDK schemas
    ├── repository.py            # owned reads, locks, message sequencing
    ├── service.py               # idempotency and two-transaction orchestration
    ├── dependencies.py          # choose configured adapter at composition edge
    ├── router.py                # /conversations and /model-calls
    └── commands/fail_stale_calls.py # manual crash-window cleanup
tests/
├── unit/core/llm/test_fake.py
├── unit/chat/test_service.py
├── integration/api/test_chat.py
└── smoke/test_anthropic_adapter.py # skipped unless explicitly enabled
```

The gateway belongs in `core/llm` because multiple future modules will call models. Conversation policy remains in `modules/chat`. Do not create a generic “AI service” that mixes provider calls, HTTP, SQL, tools, and memory.

## 5. Internal model-gateway contract

Use plain internal dataclasses and a Protocol. The request is immutable enough to hash and test. Vendor SDK types cannot cross this file boundary.

```python
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system_prompt: str
    messages: tuple[LLMMessage, ...]
    max_output_tokens: int
    temperature: float


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None


class LLMClient(Protocol):
    provider_name: str

    def generate(self, request: LLMRequest) -> LLMResult: ...
```

The Chat service decides which history, prompt, model alias, token limit, and temperature are allowed. The adapter only translates and classifies provider failures into internal exceptions such as `LLMTimeout`, `LLMRateLimited`, `LLMUnavailable`, and `LLMRejectedRequest`.

Start with a fake whose answer depends predictably on input:

```python
class FakeLLMClient:
    provider_name = "fake"

    def generate(self, request: LLMRequest) -> LLMResult:
        latest = request.messages[-1].content
        return LLMResult(
            text=f"FAKE: {latest}",
            provider_request_id="fake-request-1",
            input_tokens=sum(len(item.content.split()) for item in request.messages),
            output_tokens=len(latest.split()) + 1,
            stop_reason="end_turn",
        )
```

Add configurable fake modes that raise each internal exception. Tests should exercise the same orchestration without monkeypatching the service's internals.

## 6. Tables, invariants, and migrations

### `conversations`

- `id UUID PRIMARY KEY`; `user_id` FK with cascade
- `title VARCHAR(200) NOT NULL`, nonblank
- nullable `archived_at TIMESTAMPTZ`; timestamps
- index `(user_id, updated_at DESC, id DESC)`

### `messages`

- `id UUID PRIMARY KEY`
- `conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE`
- `sequence INTEGER NOT NULL`, positive
- `role VARCHAR(16) NOT NULL`, check `user|assistant`
- `content TEXT NOT NULL`, nonempty, API-limited to 20,000 characters for user input and configured output limit for assistant text
- `created_at TIMESTAMPTZ NOT NULL`
- unique `(conversation_id, sequence)`; index supports `(conversation_id, sequence ASC)`

Sequence allocation happens while the Conversation row is locked. Do not calculate `max(sequence)+1` without serialization.

### `model_calls`

- `id UUID PRIMARY KEY`; `user_id` and `conversation_id` FKs with cascade
- `request_message_id UUID NOT NULL UNIQUE REFERENCES messages(id)`
- nullable `response_message_id UUID UNIQUE REFERENCES messages(id)`
- `status VARCHAR(16)`: `started`, `succeeded`, `failed`
- `provider VARCHAR(50)`, `model VARCHAR(200)`; record the exact external model ID
- `idempotency_key VARCHAR(128)` and `request_fingerprint CHAR(64)`; unique `(user_id,idempotency_key)`
- `request_spec JSONB NOT NULL`: canonical message IDs/content snapshot, model parameters, and either the exact system-prompt snapshot or a reference to an immutable versioned prompt row. A hash alone is not reproducible; choose exact retention deliberately. Never store an API key.
- nullable latency milliseconds, input/output token counts, stop reason, provider request ID, finished time
- nullable stable internal `error_code` and short sanitized `error_message`; never store raw SDK exception/response by default
- timestamps and checks tying terminal state to `finished_at`

Create a PostgreSQL partial unique index on `conversation_id WHERE status='started'`. This enforces one in-flight call even when two application processes race. Cost remains null in Phase 4 unless you store a dated/versioned price snapshot; live pricing multiplied later would rewrite history.

### Normative response vocabulary

Every timestamp is a UTC RFC 3339 string with `Z`; every ID is a UUID string. Every listed key is always present and only fields typed `| null` may be null. No object accepts extra response keys.

- **`ConversationPublic`:** `id: UUID`; `title: string` 1–200; `archived_at: datetime | null`; `created_at: datetime`; `updated_at: datetime`.
- **`MessagePublic`:** `id: UUID`; `conversation_id: UUID`; `role: "user"|"assistant"`; `content: string` 1–20,000; `sequence: integer >= 1`; `created_at: datetime`.
- **`ModelCallPublic`:** `id: UUID`; `conversation_id: UUID`; `request_message_id: UUID`; `response_message_id: UUID | null`; `status: "started"|"succeeded"|"failed"`; `provider: string` 1–50; `model: string` 1–200; `latency_ms: integer >= 0 | null`; `input_tokens: integer >= 0 | null`; `output_tokens: integer >= 0 | null`; `stop_reason: string 1–100 | null`; `error_code: string 1–80 ASCII | null`; `created_at: datetime`; `finished_at: datetime | null`. `response_message_id` is non-null on success; `error_code` is non-null on failure; `finished_at` is non-null for either terminal state.
- **`SendMessageModelCallPublic`:** `id: UUID`; `status: "succeeded"`; `provider: string` 1–50; `model: string` 1–200; `input_tokens: integer >= 0 | null`; `output_tokens: integer >= 0 | null`; `latency_ms: integer >= 0`.
- **`SendMessageResponse`:** `user_message: MessagePublic`; `assistant_message: MessagePublic`; `model_call: SendMessageModelCallPublic`. Both messages have the addressed conversation ID and consecutive sequences, user first.
- **`ConversationPage`:** `items: array[ConversationPublic]` of 0–100 in `(updated_at DESC,id DESC)` order; `next_cursor: string 1–2,048 | null`.
- **`MessagePage`:** `items: array[MessagePublic]` of 0–100 in `(sequence ASC,id ASC)` order; `next_cursor: string 1–2,048 | null`.

Provider failure responses use the common error envelope with `details.model_call_id`; clients retrieve the failed `ModelCallPublic` through Endpoint 7.8 rather than receiving a partial success DTO.

Migration sequence:

1. Create `conversations` and owner-list index.
2. Create `messages` and ordering constraint/index.
3. Create `model_calls`, FKs, idempotency constraint, checks, and partial unique in-flight index.

Run downgrade/upgrade and inspect named constraints. Test cascade only after consciously accepting that conversation deletion permanently deletes message/call history.

## 7. Endpoint contract worksheets

Every route requires bearer authentication, rejects unknown JSON fields, and uses the shared error envelope. Unless an endpoint says otherwise, successful JSON responses include normal `Content-Type` and request-ID headers; they set no cookie and no cacheable credential header. Missing/invalid auth is `401 INVALID_ACCESS_TOKEN`.

Apply executable transport bounds before schema work: any JSON request body is at most 131,072 bytes; the raw query component is at most 4,096 bytes and contains at most 20 pairs; a cursor is 1–2,048 unpadded base64url ASCII characters and decodes to at most 512 bytes of UTF-8 JSON. The Conversation-list object is exactly `{"v":1,"kind":"conversations","filters_sha256":"<64 lowercase hex>","last":{"updated_at":"<UTC RFC3339>","id":"<canonical UUID>"}}`; fingerprint canonical compact JSON `{"archived":false|true}`, order `(updated_at DESC,id DESC)`, and continue with tuple `<`. The Message-list object is exactly `{"v":1,"kind":"messages","conversation_id":"<canonical UUID>","last":{"sequence":<integer >= 1>,"id":"<canonical UUID>"}}`; order `(sequence ASC,id ASC)` and continue with tuple `>`. `limit` is excluded from both bindings. A missing cursor is valid; non-string, empty, overlong, padded, or non-base64url input is `422 REQUEST_VALIDATION_FAILED`. After that surface check, invalid UTF-8/JSON, unknown/missing keys, unsupported version/kind, invalid semantic values, trailing data, conversation mismatch, or filter mismatch is `400 INVALID_CURSOR`. Query booleans are the lowercase literals `true|false`, not a broad truthy parser. All UUID path values use canonical UUID text. These rules are part of the contract and are tested at the byte boundary.

### Normative Phase 4 error vocabulary

Every error has the shared envelope and exactly one stable status/code below. Common `401 INVALID_ACCESS_TOKEN`, `422 REQUEST_VALIDATION_FAILED`, and `500 INTERNAL_SERVER_ERROR` retain their earlier meanings. `REQUEST_VALIDATION_FAILED` covers malformed UUID/JSON, unknown fields, wrong primitive types, invalid body lengths/enums, and malformed non-cursor query values unless a narrower code applies.

| Status and code | Exact condition |
|---|---|
| `401 INVALID_ACCESS_TOKEN` | Missing or invalid bearer credential. |
| `404 CONVERSATION_NOT_FOUND` | Conversation is absent or belongs to another user. |
| `404 MODEL_CALL_NOT_FOUND` | Model call is absent or belongs to another user. |
| `409 CONVERSATION_ARCHIVED` | A send is requested for an archived Conversation. |
| `409 CONVERSATION_BUSY` | Another model call is in flight, or archive/delete conflicts with one. |
| `409 IDEMPOTENCY_KEY_REUSED` | Same user/key is bound to a different request fingerprint. |
| `409 MODEL_CALL_IN_PROGRESS` | Replay addresses the same still-started logical call. |
| `413 CHAT_CONTEXT_TOO_LARGE` | Persisted context selected for the call exceeds `APP_LLM_MAX_INPUT_CHARACTERS`. |
| `422 REQUEST_VALIDATION_FAILED` | Generic path/query/body/transport schema failure described above. |
| `422 EMPTY_UPDATE` | Conversation PATCH supplies no mutable field. |
| `400 INVALID_CURSOR` | Surface-valid cursor cannot be decoded semantically or does not match current filters/order/version. |
| `422 PAGE_LIMIT_INVALID` | `limit` is not an integer in `1..100`. |
| `422 CONVERSATION_FILTER_INVALID` | `archived` is present but is not exact `true` or `false`. |
| `422 IDEMPOTENCY_KEY_INVALID` | Header is absent or not 8–128 characters in ASCII `0x21..0x7E`. |
| `429 CHAT_BUDGET_EXCEEDED` | Current user's configured local call/token/spend budget is exhausted. |
| `502 MODEL_PROVIDER_REJECTED` | Provider definitively rejects the normalized request. |
| `503 MODEL_PROVIDER_UNAVAILABLE` | Provider rate limit or transient availability failure. |
| `504 MODEL_PROVIDER_TIMEOUT` | Configured provider deadline expires. |
| `500 INTERNAL_SERVER_ERROR` | Unclassified server defect; response is sanitized. |

Framework `405` and the sanitized `500` remain common outcomes rather than repeated in each card.

### 7.1 Create Conversation

- **Purpose/method/path/auth:** create an owned chat container; `POST /api/v1/conversations`; bearer required.
- **Path/query/body:** no parameters; body may omit `title` to use `New conversation`; if present it is non-null, trimmed, 1–200. Empty `{}` is valid.
- **Success:** `201 Created`; `Location: /api/v1/conversations/{id}`; body `ConversationPublic`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 REQUEST_VALIDATION_FAILED`.
- **Tables/transaction/side effects:** insert `conversations`, service commit/refresh; no model call.
- **Tests:** default/custom title, bounds/unknown/null, owner derived from token, `Location`, no messages created.

### 7.2 List Conversations

- **Purpose/method/path/auth:** stable owner page; `GET /api/v1/conversations`; bearer.
- **Query/body:** optional non-null exact `archived=true|false`, default false; integer `limit` default 20 and range 1–100; optional non-null cursor satisfying the phase policy and bound to `archived`; no body.
- **Success:** `200 OK`; `ConversationPage`; standard JSON/request-ID headers.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 CONVERSATION_FILTER_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor decode/schema/filter mismatch.
- **Tables/transaction:** owner-scoped `conversations` read; no commit/provider call.
- **Tests:** active/archive filter, Alice/Bob isolation, tied times, complete no-duplicate pagination.

### 7.3 Retrieve Conversation

- **Purpose/method/path/auth:** read conversation metadata; `GET /api/v1/conversations/{conversation_id}`; bearer.
- **Parameters/body:** UUID path; no query/body.
- **Success:** `200 ConversationPublic`; standard headers.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID; `404 CONVERSATION_NOT_FOUND`.
- **Tables/transaction:** owner-scoped `conversations` select; no commit.
- **Tests:** owned/missing/malformed/foreign and no message content in this metadata response.

### 7.4 Patch Conversation

- **Purpose/method/path/auth:** rename or archive/unarchive; `PATCH /api/v1/conversations/{conversation_id}`; bearer.
- **Body:** at least one of `title` non-null trimmed 1–200 or `archived` non-null boolean; unknown fields forbidden.
- **Success:** `200`, updated `ConversationPublic`; standard headers.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `404 CONVERSATION_NOT_FOUND`; `422 EMPTY_UPDATE`; `422 REQUEST_VALIDATION_FAILED`; `409 CONVERSATION_BUSY`.
- **Tables/transaction:** lock owned `conversations`, inspect in-flight `model_calls` when archiving, update, commit once.
- **Tests:** rename/archive/unarchive, empty/null/unknown, busy conflict and rollback, isolation.

### 7.5 Delete Conversation

- **Purpose/method/path/auth:** permanently erase one owned conversation and its retained content; `DELETE /api/v1/conversations/{conversation_id}`; bearer.
- **Parameters/body:** UUID path; no query/body.
- **Success:** `204 No Content`; empty body; standard request-ID header only.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `404 CONVERSATION_NOT_FOUND`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID; `409 CONVERSATION_BUSY`.
- **Tables/transaction/side effects:** lock/delete `conversations`; cascade `messages`/`model_calls`; commit. It cannot retract content already sent to a provider.
- **Tests:** cascade, busy conflict, foreign privacy, exact empty `204`, repeated delete `404`.

### 7.6 Send Message (non-streaming chat)

- **Purpose/method/path/auth:** persist a user turn, invoke configured adapter exactly once, persist assistant outcome; `POST /api/v1/conversations/{conversation_id}/messages`; bearer.
- **Headers/path/query:** required `Idempotency-Key`, 8–128 characters each in non-space printable ASCII `0x21..0x7E`; do not trim or normalize it. UUID path; no query parameters. Same key is scoped to user.
- **Body:** `{content}` only; non-null string, trimmed but preserve intentional internal newlines, 1–20,000 characters. Provider/model/role/user ID are forbidden.
- **Success:** first execution is `201 Created`; `Location: /api/v1/model-calls/{model_call_id}`; body `SendMessageResponse`; request-ID header. An identical completed replay returns the same `201` representation and Location plus `Idempotency-Replayed: true`; it does not create anything new.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 IDEMPOTENCY_KEY_INVALID`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID/body or content above 20,000 characters; `413 CHAT_CONTEXT_TOO_LARGE`; `404 CONVERSATION_NOT_FOUND`; `409 CONVERSATION_ARCHIVED`; `409 IDEMPOTENCY_KEY_REUSED`; `409 MODEL_CALL_IN_PROGRESS` with safe call ID; `409 CONVERSATION_BUSY`; `429 CHAT_BUDGET_EXCEEDED`; `504 MODEL_PROVIDER_TIMEOUT`; `503 MODEL_PROVIDER_UNAVAILABLE`; `502 MODEL_PROVIDER_REJECTED`. Provider failures include safe `details.model_call_id`.
- **Tables/transactions/side effects:** transaction A locks/selects `conversations`, checks `model_calls`, inserts user `messages` plus started `model_calls`, snapshots request, commits. One external model request happens outside a transaction. Transaction B inserts assistant `messages` and succeeds the call, or records failure, then commits. Never roll back the already committed user turn because the provider failed.
- **Tests:** fake success; every fake failure remains queryable; exact persistence/order; idempotent successful replay returns stored outcome without fake call count increasing; pending/different-payload conflicts; concurrent sends; archived/foreign; size limits; no secrets/raw errors.

### 7.7 List Conversation Messages

- **Purpose/method/path/auth:** retrieve stable chronological history; `GET /api/v1/conversations/{conversation_id}/messages`; bearer.
- **Parameters/body:** UUID path; integer `limit` default 50 and range 1–100; optional non-null cursor satisfying the phase policy and containing `(sequence,id)` plus the conversation binding; no body.
- **Success:** `200 MessagePage`; standard headers.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `404 CONVERSATION_NOT_FOUND`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID or cursor surface type/length/alphabet; `422 PAGE_LIMIT_INVALID`; `400 INVALID_CURSOR` for cursor decode/schema/conversation mismatch.
- **Tables/transaction:** owner-check `conversations`, read `messages`; no commit/provider call.
- **Tests:** empty/history, exact role/order, pagination ties impossible due unique sequence, isolation, failed call shows user message but no assistant message.

### 7.8 Retrieve Model Call

- **Purpose/method/path/auth:** inspect safe operational metadata, especially after an error; `GET /api/v1/model-calls/{model_call_id}`; bearer.
- **Parameters/body:** UUID path only; no query/body.
- **Success:** `200 ModelCallPublic`; exclude request snapshot/content and provider credentials; standard headers.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID; `404 MODEL_CALL_NOT_FOUND`.
- **Tables/transaction:** owner-scoped `model_calls` read; no mutation.
- **Tests:** started/succeeded/failed views, isolation, safe nullable fields, response excludes API key/raw provider body/request snapshot.

## 8. Build vertical slices

### Slice A — Conversation resource

Build migration, model, schema, repository, service, router, and tests for create/get/list before adding messages. Then patch/archive/delete. Repository signatures always take `owner_id`. Keep list ordering/cursor rules identical to earlier modules.

### Slice B — Deterministic gateway and fake

Write the dataclasses/Protocol, fake, and fake tests. Add a composition function that returns `FakeLLMClient` when configured. Do not conditionally import Anthropic at module import time when fake mode is selected; developers without the optional key should still run tests.

### Slice C — Message persistence without a network

Add message/call migrations. Build transaction A: lock owned active Conversation, normalize idempotency key, compute a canonical SHA-256 fingerprint, enforce one in-flight call, allocate sequence, save user message and exact request snapshot, and commit. Use canonical JSON (`sort_keys=True`, fixed separators, UTF-8) so equivalent data hashes consistently.

Build the `LLMRequest` from the persisted snapshot, not a second live query that may observe concurrent changes. Call the fake. Build transaction B: lock call and Conversation, allocate assistant sequence, save assistant message, finalize counts/latency, and commit.

Measure duration with `time.monotonic_ns()`, not wall-clock subtraction. Wall time is for timestamps; a monotonic clock is for elapsed duration.

### Slice D — Failure and idempotent replay

Catch only known internal gateway exceptions. In a new transaction mark the call failed with a stable code and safe message, commit it, then raise a domain error for the shared HTTP mapper. Unexpected exceptions still need a generic failed state where possible, structured `logger.exception` with IDs, and `500 INTERNAL_SERVER_ERROR`; do not persist `repr(exception)`.

On a repeated key: compare fingerprints with `compare_digest`; if different return `409 IDEMPOTENCY_KEY_REUSED`; if succeeded return the stored original messages/status without a provider call; if failed return the same classified failure without retry; if started return `409 MODEL_CALL_IN_PROGRESS`. A deliberate retry uses a new idempotency key.

### Slice E — Manual stale-call repair

Create `fail_stale_calls` to lock calls left `started` beyond a configurable threshold, mark them failed with `MODEL_CALL_INTERRUPTED`, and commit. It must not invoke a provider: the prior request may actually have been billed, and its outcome is unknown. A future worker/provider idempotency mechanism can improve recovery.

## 9. Integrate one real provider: Anthropic

Do this only after every fake-based test is green.

1. Read the current [Anthropic Python SDK page](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python), [Messages API](https://platform.claude.com/docs/en/api/messages), [errors](https://platform.claude.com/docs/en/api/errors), [rate limits](https://platform.claude.com/docs/en/api/rate-limits), and [model overview](https://platform.claude.com/docs/en/about-claude/models/overview). Record the date and exact SDK/model selection in an ADR; model IDs and capabilities change.
2. Create a restricted development API key in the provider console. Put it only in the local environment/secret manager. Configure spending alerts/limits where available.
3. Install the SDK range above. Set `APP_LLM_PROVIDER=anthropic`, `APP_LLM_MODEL=<an exact currently documented model ID>`, and the key. Never copy a model ID blindly from this chapter.
4. Construct one synchronous SDK client for the process with the configured 30-second timeout and `max_retries=0`. The official SDK currently retries some connection, `408`, `409`, `429`, and server failures by default; disabling SDK retries makes Phase 4's “one intended attempt” observable. Revisit retry policy only with cost/idempotency analysis.
5. Translate internal messages into the current Messages request. Supply system prompt separately, `max_tokens`, exact model, and temperature. Extract only text blocks; record usage, stop reason, and the documented public request ID. If no text block exists, classify it rather than stringify SDK objects.
6. Map documented SDK exception classes to internal exceptions. Log provider name, internal call ID, safe status class, and provider request ID—not key, prompt, output, headers, or raw error body.
7. Run one explicit smoke test with a harmless prompt and a small output cap. Inspect persisted usage/latency. Switch back to fake for normal development.

The adapter shape is intentionally small:

```python
from anthropic import Anthropic, APITimeoutError, RateLimitError


class AnthropicLLMClient:
    provider_name = "anthropic"

    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._client = Anthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def generate(self, request: LLMRequest) -> LLMResult:
        try:
            response = self._client.messages.create(
                model=request.model,
                system=request.system_prompt,
                messages=[
                    {"role": item.role, "content": item.content}
                    for item in request.messages
                ],
                max_tokens=request.max_output_tokens,
                temperature=request.temperature,
            )
        except APITimeoutError as exc:
            raise LLMTimeout() from exc
        except RateLimitError as exc:
            raise LLMRateLimited() from exc

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        if not text:
            raise LLMInvalidResponse()
        return LLMResult(
            text=text,
            provider_request_id=response._request_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )
```

Complete the adapter by handling the current documented connection/status error classes. Do not use a broad catch to label programming errors as provider outages. Keep all vendor imports and response-property knowledge in this module.

Because this project uses normal `def` routes and synchronous SQLAlchemy, the synchronous SDK is a coherent Phase 4 choice. FastAPI runs those route functions in its thread pool. Load-test before changing concurrency architecture; switching only one function to `async def` while making blocking SDK/database calls would block the event loop.

## 10. Reliability, security, and transaction rules

- Service methods own both short database transactions and explicitly rollback on database failure; repositories never commit.
- The provider call occurs with no SQLAlchemy transaction/row lock held.
- Put a hard input-character limit, output-token cap, timeout, one-in-flight constraint, and per-user rate/budget hook in place before Internet exposure.
- Do not automatically retry in Phase 4. A timeout means the provider outcome and billing may be unknown.
- Store exact provider/model and request configuration snapshot. Do not recalculate historical calls from current settings.
- Prompt and response content are personal data. Define retention/deletion behavior; metadata logs exclude them by default.
- Model output is untrusted. Render it safely in the frontend and never evaluate it as code/SQL/HTML.
- Never accept provider, system prompt, role, token budget, or user ID directly from an unrestricted client field.
- Never expose provider errors verbatim. Stable internal codes form your API contract.

## 11. Test strategy

**Unit:** gateway value types; deterministic fake success and classified failures; canonical fingerprint; service transaction A/B sequencing with fakes; idempotent replay matrix; exception mapping; monotonic latency; stale-call rule. Assert the fake's call count.

**PostgreSQL integration:** FKs/checks/cascades; sequence uniqueness under concurrent Sessions; partial in-flight uniqueness; `(user,key)` uniqueness; rollback behavior; owner-scoped call/message queries; request-spec JSON round trip.

**HTTP contracts:** every worksheet, exact `Location`/`204`/error headers and bodies; Alice/Bob isolation; archive/busy conflicts; duplicate keys; all fake failure mappings; persisted failed run ID; content/limit validation; no vendor secrets or raw errors.

**Provider smoke:** mark with `@pytest.mark.external` and skip unless an explicit flag plus key is present. It is not part of CI or the normal `pytest` command. Assert only contract shape, not exact prose. Never use a real adapter in reliability tests.

## 12. Manual curl and debugging

```bash
curl -i -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Backend learning"}' \
  http://127.0.0.1:8000/api/v1/conversations

curl -i -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: local-turn-0001' \
  -d '{"content":"Explain a database transaction in two sentences."}' \
  http://127.0.0.1:8000/api/v1/conversations/REPLACE_ID/messages
```

Repeat the second command unchanged: the fake call counter/log event must not increase. Repeat the key with different content: expect `409`. Select rows in `messages` and `model_calls` to connect HTTP output to transaction state. Activate each fake failure and retrieve the returned model-call ID.

Debug by boundary: if persistence is wrong, inspect transaction A and sequence lock; if no adapter is selected, inspect safe provider/model configuration (never dump key); if output mapping is wrong, inspect adapter tests with constructed vendor response fixtures; if timeout status is wrong, inspect exception classification; if a duplicate cost occurs, compare user/key/fingerprint and the unique constraint. Correlate your request ID, model-call ID, and sanitized provider request ID.

## 13. Optional streaming—only after non-streaming passes

Streaming changes the contract. Specify Server-Sent Events, event names, disconnect behavior, partial-output retention, terminal error events, proxy buffering/timeouts, and when a `model_call` becomes succeeded. A disconnect does not prove the provider stopped billing. Build a separate endpoint or content negotiation only after writing those worksheets and tests. Do not retrofit `yield` into the service and hope transaction/idempotency semantics remain correct.

## 14. Exercises

1. Draw the two transactions and mark every possible crash point and resulting database state.
2. Make the fake count calls; prove retries with one key call it once.
3. Deliberately hold a transaction during a sleeping fake, inspect pool pressure, then repair the design.
4. Add a second toy adapter without changing Chat service imports. Explain what the port bought you.
5. Simulate timeout, rate limit, invalid response, and unexpected programming error; verify four honest outcomes.
6. Run two simultaneous sends to one Conversation and prove the partial unique index, not a Python global, resolves the race.
7. Write a retention ADR covering deletion, logs, provider retention assumptions, and future memory extraction.
8. Design—but do not implement—the SSE event contract and disconnect matrix.

## 15. Checkpoint

Phase 4 is complete when clean migrations rebuild; all eight endpoints meet their worksheets; Conversations/Messages/Calls are owner-scoped and survive restart; the fake is deterministic; the provider call occurs outside transactions; failures persist a safe terminal call; repeated idempotency keys cannot repeat a call; concurrency constraints work in PostgreSQL; logs contain no key/prompt/output/raw provider error; normal tests use no network; and one optional real-provider smoke call succeeds under an explicit budget.

## What you should be able to explain after Phase 4

- Port/adapter dependency inversion and why SDK types stay at the edge.
- Why tests use a fake and why temperature zero is not a testing guarantee.
- The two-transaction model, its crash window, and why network waits do not belong in DB transactions.
- Idempotency key, request fingerprint, database uniqueness, and one-in-flight policy.
- Conversation ownership, stable message ordering, and request snapshots.
- Timeout, rate limit, unavailable, invalid response, safe error mapping, and uncertain provider outcome.
- Token usage, latency measurement, exact model identity, cost-history caveats, and sensitive-data retention.
- Why streaming is a different protocol rather than a boolean implementation detail.

## Official reading

- [Anthropic Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)
- [Anthropic Messages API reference](https://platform.claude.com/docs/en/api/messages)
- [Anthropic API errors](https://platform.claude.com/docs/en/api/errors)
- [Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits)
- [Anthropic model overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [FastAPI concurrency and `def`/`async def`](https://fastapi.tiangolo.com/async/)
- [FastAPI response headers](https://fastapi.tiangolo.com/advanced/response-headers/)
- [SQLAlchemy Session transaction management](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [PostgreSQL partial indexes](https://www.postgresql.org/docs/current/indexes-partial.html)
- [PostgreSQL `jsonb`](https://www.postgresql.org/docs/current/datatype-json.html)
- [RFC 9110: HTTP semantics and idempotent methods](https://www.rfc-editor.org/rfc/rfc9110)
- [W3C Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html)

Provider SDKs, model catalogs, rate limits, and pricing are live data. Re-open the primary documentation when implementing the real adapter; record the date/model/SDK decision and keep this internal contract stable when vendor details move.
