# Phase 5 — Tool Registry, Durable Jobs, Approvals, and Audit

This phase turns ordinary application functions into **controlled capabilities** that an agent may propose. Build it after authentication, owner-scoped tasks, reminders, and the Phase 4 model gateway. A model is an untrusted proposer: it never becomes the authority that permits a write.

## Outcome, prerequisites, and non-goals

By the end, you can register an allowlisted `list_tasks` read tool and `create_task` write tool, create runs through HTTP, require an exact approval for writes, claim work safely with two worker processes, retry known-safe failures, cancel honestly, and inspect an immutable audit trail.

Prerequisites:

- Phase 2 authentication exposes a `CurrentUser` dependency.
- Phase 3 has owner-scoped task services. A tool calls the service; it does not bypass it with its own SQL.
- Phase 4 can store a model-proposed tool name and arguments, although this phase starts with human-created runs.
- PostgreSQL, SQLAlchemy 2, Alembic, Pydantic 2, pytest, and the common error envelope already work.

Non-goals: arbitrary user plugins, shell execution, dynamic imports, distributed orchestration, exactly-once execution, and autonomous approval. Do not start `agent_runs` until manual tool runs are trustworthy.

## 1. Concepts to understand before coding

A **tool definition** is metadata for one capability: stable name and version, description, risk, input/output JSON Schemas, and a safe handler key. A **code registry** maps the handler key to a reviewed Python object. The database may enable a known definition; it may not contain executable Python, import paths, shell commands, SQL, or arbitrary URLs supplied by a client.

A **tool run** is one requested business operation. A **worker attempt** is one process's attempt to execute that run. Keeping them separate lets one run have several attempts without erasing the history.

Durable work means the intent exists in PostgreSQL before an API says `202 Accepted`. If the API or worker restarts, another worker can continue. An in-memory `BackgroundTasks` callback cannot provide that promise.

A **lease** is temporary ownership, not success. A worker atomically claims a due row, records `lease_owner` and `lease_expires_at`, commits, executes outside the transaction, then records the outcome. A crashed worker stops heartbeating; after expiry, a later worker can recover the row. This produces **at-least-once** attempts. Exactly-once external effects generally require cooperation from the target through an idempotency key or reconciliation API.

An **idempotency key** identifies the caller's logical request. Repeating the same key and same canonical input returns the original run. Reusing the key for different input is `409 IDEMPOTENCY_KEY_REUSED`.

An approval must be bound to what was reviewed. Bind it to `tool_definition_id`, `tool_version`, and a hash of canonical validated input. Approval of `create_task v1 {"title":"Read"}` must not authorize v2 or a changed title.

## 2. Architecture and state machines

```text
HTTP router -> ToolRunService -> repositories -> PostgreSQL
                     |                ^
                     v                |
             code ToolRegistry        |
                                      |
worker -> claim/lease service -> execute handler -> finalize service
                                      |
                                      -> existing TasksService
```

The router translates HTTP. The service owns authorization, policy, state transitions, and commits/rollbacks. Repositories issue SQL and `flush()` but never commit. The registry resolves only hard-coded handler keys. The worker is a second composition root using the same services and session factory.

Run state machine:

```text
requested -> queued                         policy permits a read
requested -> awaiting_approval              write/destructive risk
awaiting_approval -> queued | rejected
queued -> running | cancelled_before_start
running -> queued                         # known-safe delayed retry
running -> succeeded | failed | outcome_unknown
running -> cancel_requested -> succeeded | failed | outcome_unknown
```

`rejected`, `cancelled_before_start`, `succeeded`, `failed`, and `outcome_unknown` are terminal for this run. A return to `queued` is allowed only for a classified known-safe retry with attempts remaining; its future `next_attempt_at` prevents immediate reclaim. A queued cancellation proves the handler never started. A running cancellation is only a request; Python cannot retract a request that already left the process. `outcome_unknown` means the worker lost evidence after a possibly-started side effect. Do not lie by calling it cancelled or failed. Reconcile with the target, if possible, before a human chooses a new run.

Attempt state is smaller: `claimed -> executing -> succeeded|retryable_failure|permanent_failure|lease_expired|outcome_unknown`. Every claim inserts a new attempt row.

## 3. Dependencies and folder changes

Add one runtime dependency to `pyproject.toml`:

```toml
"jsonschema>=4.23,<5",
```

Install again with `python -m pip install -e ".[dev]"`. Use JSON Schema Draft 2020-12 consistently. Pydantic remains the HTTP contract; JSON Schema validates generic tool input at the registry boundary.

Add these files incrementally:

```text
app/
├── modules/tools/
│   ├── model.py                 # definition, run, decision, attempt, audit rows
│   ├── schemas.py               # safe HTTP contracts
│   ├── repository.py            # owner-scoped SQL and locking
│   ├── service.py               # policy, transitions, transaction ownership
│   ├── registry.py              # allowlisted handler map and schema validation
│   ├── canonical.py             # one versioned canonicalization/hash function
│   ├── policy.py                # risk -> approval decision
│   ├── dependencies.py
│   └── router.py
├── workers/
│   ├── tool_worker.py           # Phase 5 loop now; executor/handler after Phase 8 consolidation
│   └── tool_executor.py         # claim, invoke, finalize one run
└── core/clock.py                # injectable UTC clock for deterministic tests
tests/
├── unit/tools/
├── integration/tools/
└── contract/tools/
```

Do not create a generic `utils.py`. Names reveal ownership.

## 4. Database design and migration

Create one reviewed Alembic revision containing these tables. Prefer PostgreSQL `CHECK` constraints for small stable state sets while learning; an application enum alone does not protect non-HTTP writes.

### `tool_definitions`

- `id uuid` primary key.
- `name varchar(100)` and `version integer`; unique together.
- `description text`, `risk varchar(20)` constrained to `read|write|destructive`.
- `input_schema jsonb`, `output_schema jsonb`.
- `handler_key varchar(120)`, `enabled boolean`, timestamps.
- Unique `handler_key, version`. Database metadata never turns an unknown key into executable code.

Seed definitions with a reviewed migration or an explicit startup/admin sync command—not on every web request. Never accept a create-definition endpoint.

### `tool_runs`

- `id uuid`, `user_id` FK, optional `conversation_id` FK **with `ON DELETE SET NULL`**, `tool_definition_id` FK. The nullable link is context, not ownership: deleting a Phase 4 conversation detaches the run but preserves its audit/execution record.
- `requester` constrained to `human|model`; `status` constrained to the run states above.
- `canonical_input jsonb`, `canonicalizer_version varchar(20)`, `input_hash char(64)`.
- `idempotency_key varchar(200)`, unique on `(user_id, idempotency_key)`.
- Durable work fields: `attempt_count` non-null default `0`, `max_attempts` non-null default `3`, `next_attempt_at timestamptz` non-null default PostgreSQL `now()`, `lease_owner`, `lease_expires_at`, `heartbeat_at`, `cancel_requested_at`, `started_at`, `finished_at`.
- `safe_output jsonb`, `safe_error_code`, `safe_error_message`, `result_truncated boolean`.
- timestamps and optional optimistic `row_version integer`.

Add checks: attempt counts are non-negative and never exceed max attempts; max attempts is at least one; `next_attempt_at` is never null; a lease owner and expiry are both null or both present; `finished_at` is required only for terminal states. Index `(status, next_attempt_at, created_at)` for the worker and `(user_id, created_at desc, id desc)` for cursor history.

### `tool_run_decisions`

- `id`, `tool_run_id` unique FK, `decided_by_user_id` FK.
- `decision` constrained to `approved|rejected`, exact `tool_definition_id`, `tool_version`, `canonicalizer_version`, `input_hash`.
- optional bounded reason and `created_at`.

Never update or delete a decision. A changed proposal creates a new run.

### `tool_run_attempts`

- `id`, `tool_run_id` FK, `attempt_number`, unique together.
- worker/lease identity, claim/start/heartbeat/end timestamps, including nullable `handler_started_at` set immediately before invocation.
- outcome, safe error, provider request ID if relevant, duration.

### `tool_audit_events`

- `id bigserial`, `tool_run_id` FK, actor type/id, event type.
- `from_status`, `to_status`, request ID, safe `details jsonb`, `created_at`.

Append only. Audit details exclude credentials, authorization headers, full message bodies, and raw stack traces.

For the Phase 5 `create_task` exercise, also add nullable unique `tasks.source_tool_run_id` referencing `tool_runs.id` with `ON DELETE RESTRICT`. The executor writes the task and successful run result in one transaction, so a crash rolls both back; the unique source is defensive idempotency.

Migration exercise: write the table plan in SQL first, generate Alembic, then inspect every FK, unique constraint, server default, index, and downgrade. Explicitly declare `ForeignKey("conversations.id", ondelete="SET NULL")` for `tool_runs.conversation_id`; do not rely on ORM cascade or a database default. Run upgrade, downgrade, and upgrade against a disposable database.

## 5. The endpoint contract gate

All client JSON schemas use Pydantic `ConfigDict(extra="forbid")`; unknown fields return `422`. No top-level field accepts `null` unless its endpoint card explicitly says so, such as create-run `conversation_id` or a decision/cancellation reason. Generic `input` may contain `null` only when that tool's JSON Schema permits it.

Every JSON request body is at most 65,536 bytes. The raw query component is at most 4,096 bytes and 20 pairs. Every opaque cursor is 1–2,048 unpadded base64url ASCII characters and decodes to at most 512 bytes of UTF-8 JSON containing exactly version, filter fingerprint, and last sort tuple. Non-string, empty, overlong, padded, or non-base64url cursor input is `422 REQUEST_VALIDATION_FAILED`; after that surface check, invalid UTF-8/JSON, unknown/missing keys, unsupported versions, malformed semantic values, trailing data, or filter mismatch is `400 INVALID_CURSOR`. UUID path/query fields use canonical UUID text. These bounds are enforced before repository work.

Apply these limits **before** JSON Schema evaluation: the complete create-run HTTP body is at most 65,536 bytes; canonical UTF-8 `input` is at most 32,768 bytes; maximum nesting depth is 8 (root object is depth 1); maximum total JSON values is 1,000; each object has at most 100 keys; each key is 1–100 UTF-8 bytes; each array has at most 100 items; and each string is at most 8,192 UTF-8 bytes. Integers must be within `[-(2^53-1), 2^53-1]`; raw JSON fractional/exponent numbers are rejected in v1—represent exact decimal domain values as schema-validated strings. Booleans and null count as values. A tool schema may impose tighter limits but never relax these application limits. Return at most 20 validation issues, each with a JSON Pointer no longer than 256 characters and a message no longer than 300 characters.

Handler output uses the same depth/key/array/string/integer limits and at most 65,536 canonical UTF-8 bytes. If it exceeds a limit, store no partial output, set `result_truncated=true`, and finish with `TOOL_OUTPUT_LIMIT_EXCEEDED`; arbitrary JSON is never byte-truncated into invalid data. Path IDs are UUIDs and query enums are closed sets. All responses include `X-Request-ID`. Auth is `Authorization: Bearer <access-token>`. Errors use the existing `{ "error": { "code", "message", "details", "request_id" } }` envelope.

### Normative response vocabulary

Every timestamp below is a UTC RFC 3339 string with `Z`. Every listed key is always present; only fields typed `| null` may be JSON null. Responses use no additional keys until a versioned contract change.

- **`ToolDefinitionPublic`:** `name: string` matching `^[a-z][a-z0-9_]{0,99}$`; `version: integer >= 1`; `description: string` of 1–2,000 characters; `risk: "read"|"write"|"destructive"`; `input_schema: object`; `output_schema: object`. Each schema is at most 65,536 canonical bytes. It never contains `handler_key` or enablement/permission internals.
- **`ToolRefPublic`:** `name: string`; `version: integer >= 1`; `risk: "read"|"write"|"destructive"`.
- **`SafeErrorPublic`:** `code: string` of 1–80 ASCII characters; `message: string` of 1–500 characters; `details: object | null` with at most 20 keys and 8,192 canonical bytes. It never contains exception class, traceback, provider body, or secret.
- **`ToolRunSummary`:** `id: UUID`; `tool: ToolRefPublic`; `requester: "human"|"model"`; `status: "requested"|"awaiting_approval"|"queued"|"running"|"cancel_requested"|"rejected"|"cancelled_before_start"|"succeeded"|"failed"|"outcome_unknown"`; `attempt_count: integer >= 0`; `created_at: datetime`; `started_at: datetime | null`; `finished_at: datetime | null`.
- **`ToolRunPublic`:** every `ToolRunSummary` key plus `conversation_id: UUID | null`; `input: object` containing the exact canonical validated, safe-to-display input; `canonicalizer_version: string` of 1–20 ASCII characters; `input_hash: 64-character lowercase hex string`; `decision_required: boolean`; `max_attempts: integer >= 1`; `next_attempt_at: datetime` (always present, matching the non-null durable column; status determines whether it is currently claimable); `safe_output: object|array|string|integer|boolean|null`; `safe_error: SafeErrorPublic | null`; `result_truncated: boolean`; `status_url: string` equal to `/api/v1/tool-runs/{id}`. `safe_output` is non-null only on success unless the declared output itself is null; therefore also use status to interpret a null output.
- **`DecisionPublic`:** `id: UUID`; `tool_run_id: UUID`; `decision: "approved"|"rejected"`; `decided_by_user_id: UUID`; `tool_name: string`; `tool_version: integer >= 1`; `canonicalizer_version: string`; `input_hash: 64-character lowercase hex`; `reason: string of 0–500 characters | null`; `created_at: datetime`; `run_status: "queued"|"rejected"`.
- **`ToolAttemptPublic`:** `attempt_number: integer >= 1`; `outcome: "executing"|"succeeded"|"retryable_failure"|"permanent_failure"|"lease_expired"|"outcome_unknown"`; `started_at: datetime`; `finished_at: datetime | null`; `duration_ms: integer >= 0 | null`; `safe_error: SafeErrorPublic | null`. Worker and lease identities are never public.
- **`ToolAuditEventPublic`:** `sequence: integer >= 1`; `event_type: string` of 1–80 ASCII characters; `actor_type: "user"|"model_gateway"|"worker"|"system"`; `from_status: ToolRunSummary.status | null`; `to_status: ToolRunSummary.status | null`; `details: object | null` limited like safe error details; `created_at: datetime`.
- **`ToolRunDetail`:** every `ToolRunPublic` key plus `decision: DecisionPublic | null`; `attempts: array[ToolAttemptPublic]` ordered by attempt number and capped at `max_attempts`; `audit_events: array[ToolAuditEventPublic]` ordered by sequence and capped at 200. If history later exceeds 200, add a separate paginated audit endpoint rather than silently omitting events.

Tool input schemas must not accept passwords, OAuth tokens, API keys, or arbitrary credentials; use an owned credential/connection UUID. That makes `ToolRunPublic.input` safe for the approving user to inspect without a lossy redaction that would invalidate its hash.

### Normative Phase 5 error vocabulary

Every HTTP error uses the common envelope and one exact status/code below. Common `401 INVALID_ACCESS_TOKEN`, `422 REQUEST_VALIDATION_FAILED`, and `500 INTERNAL_SERVER_ERROR` retain their earlier meanings. Generic validation covers malformed UUID/JSON, unknown fields, wrong types, malformed names/versions/hashes/reasons, and body/query transport limits unless a narrower code applies.

| Status and code | Exact condition |
|---|---|
| `401 INVALID_ACCESS_TOKEN` | Missing or invalid bearer credential. |
| `403 TOOL_FORBIDDEN` | User is authenticated but policy forbids requesting that otherwise visible tool. |
| `403 APPROVAL_FORBIDDEN` | Actor cannot decide this run or destructive recent-auth requirement is unmet. |
| `404 TOOL_NOT_FOUND` | Definition/version is absent, disabled, or hidden from this user. |
| `404 TOOL_RUN_NOT_FOUND` | Run is absent or belongs to another user. |
| `404 CONVERSATION_NOT_FOUND` | Non-null create-run conversation is absent or belongs to another user. |
| `409 IDEMPOTENCY_KEY_REUSED` | Same user/key is bound to a different definition/input hash. |
| `409 RUN_NOT_AWAITING_APPROVAL` | Decision targets a run not currently awaiting approval. |
| `409 DECISION_ALREADY_EXISTS` | Immutable decision already exists, including a concurrent winner. |
| `409 APPROVAL_BINDING_MISMATCH` | Submitted version/canonicalizer/input hash differs from the stored proposal. |
| `409 RUN_TERMINAL` | Cancellation targets a terminal run. |
| `422 REQUEST_VALIDATION_FAILED` | Generic path/query/header/body schema failure described above. |
| `400 INVALID_CURSOR` | Surface-valid cursor cannot be decoded semantically or does not match current filters/order/version. |
| `422 PAGE_LIMIT_INVALID` | `limit` is not an integer in `1..100`. |
| `422 TOOL_FILTER_INVALID` | `risk`, status, tool name, or `created_after` filter is invalid. |
| `422 IDEMPOTENCY_KEY_INVALID` | Header is absent or not 1–200 characters in ASCII `0x21..0x7E`. |
| `422 TOOL_INPUT_INVALID` | Top-level request is valid but generic `input` violates the selected tool schema/application JSON limits. |
| `429 TOOL_RUN_QUOTA_EXCEEDED` | User's configured queued/rate/budget quota rejects a new run. |
| `500 INTERNAL_SERVER_ERROR` | Unclassified server defect; response is sanitized. |

`TOOL_OUTPUT_LIMIT_EXCEEDED` is a persisted safe worker outcome, not a create-run HTTP response. Framework `405` and sanitized `500` remain common outcomes rather than repeated per card.

### Endpoint 1 — List available tools

- **Purpose:** discover enabled definitions the current user is authorized to request.
- **Method/path:** `GET /api/v1/tools`.
- **Auth:** required; permissions filter results.
- **Parameters:** optional non-null `risk: read|write|destructive`; `limit` integer default 20 and range 1–100; optional non-null opaque base64url cursor 1–2,048 characters bound to the risk filter and `(name ASC,version DESC)` order.
- **Request body:** none.
- **Response:** `200 {"items": array[ToolDefinitionPublic], "next_cursor": string | null}` using the normative vocabulary above.
- **Headers:** request ID; normal cache policy is `private, no-store` because permissions can change.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; `422 TOOL_FILTER_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor decode/schema/filter mismatch.
- **Tables:** read `tool_definitions` plus permission data.
- **Transaction:** request-scoped read transaction; rollback/close at end.
- **Side effects:** none.
- **Tests:** anonymous; disabled tool hidden; unauthorized tool hidden; risk filter; cursor boundary; handler key absent.

### Endpoint 2 — Retrieve one definition

- **Purpose:** inspect the exact current version before constructing input.
- **Method/path:** `GET /api/v1/tools/{name}`.
- **Auth:** required.
- **Parameters:** path `name` must match `^[a-z][a-z0-9_]{0,99}$`; optional non-null integer `version >= 1`, otherwise latest enabled authorized version; no other query parameters.
- **Body:** none.
- **Response:** `200 ToolDefinitionPublic`.
- **Headers:** `X-Request-ID`, `Cache-Control: private, no-store`.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; `404 TOOL_NOT_FOUND` for absent, disabled, or unauthorized definitions (avoid capability enumeration); `422 REQUEST_VALIDATION_FAILED` for malformed name/version.
- **Tables:** read definitions/permissions.
- **Transaction/side effects:** read only; none.
- **Tests:** latest selection; exact version; disabled and other-permission both look 404; no executable metadata.

### Endpoint 3 — Request a tool run

- **Purpose:** durably record one validated manual or model proposal.
- **Method/path:** `POST /api/v1/tool-runs`.
- **Auth:** required.
- **Parameters:** required `Idempotency-Key` header, 1–200 characters each in printable non-space ASCII `0x21..0x7E`; do not trim or normalize it.
- **Body:** required non-null `tool_name` matching `^[a-z][a-z0-9_]{0,99}$`, integer `tool_version >= 1`, and object `input`; `requester` is server-derived for ordinary clients; optional nullable owned UUID `conversation_id`. Reject unknown top-level fields. Do not let ordinary clients claim `requester=model` unless the trusted model-gateway service supplies that context.
- **Response:** `202 ToolRunPublic` with `status=queued` for policy-approved reads or `awaiting_approval` for writes/destructive actions. `safe_output`, `safe_error`, `started_at`, and `finished_at` are null.
- **Headers:** `Location: /api/v1/tool-runs/{id}`, request ID; echo no secrets.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; `403 TOOL_FORBIDDEN`; `404 TOOL_NOT_FOUND`; `404 CONVERSATION_NOT_FOUND`; `409 IDEMPOTENCY_KEY_REUSED`; `422 IDEMPOTENCY_KEY_INVALID`; `422 REQUEST_VALIDATION_FAILED` for malformed top-level fields/body; `422 TOOL_INPUT_INVALID` with bounded field paths; `429 TOOL_RUN_QUOTA_EXCEEDED`.
- **Tables:** read definition/permissions; insert run and audit event.
- **Transaction:** service validates first, then inserts run+audit and commits once. Unique-key race is caught and resolved by loading the winner.
- **Side effects:** only durable queue intent—handler never runs in the HTTP process.
- **Tests:** invalid schema; unknown property rejected when schema says so; direct read path queues; write waits; same key/same input returns same run; changed input conflicts; concurrent duplicate requests create one row.

### Endpoint 4 — List the current user's runs

- **Purpose:** browse redacted execution history.
- **Method/path:** `GET /api/v1/tool-runs`.
- **Auth:** required.
- **Parameters:** optional non-null status from the exact `ToolRunSummary.status` enum; optional non-null `tool_name` matching `^[a-z][a-z0-9_]{0,99}$`; optional non-null offset-aware RFC 3339 `created_after` used as an exclusive lower bound; `limit` integer default 20, range 1–100; optional non-null opaque base64url cursor 1–2,048 characters bound to every filter and `(created_at DESC,id DESC)`.
- **Body:** none.
- **Response:** `200 {"items": array[ToolRunSummary], "next_cursor": string | null}`.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; `422 TOOL_FILTER_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor decode/schema/filter mismatch.
- **Tables:** owner-scoped read of runs joined to definitions.
- **Transaction/side effects:** read only; none.
- **Tests:** user isolation; deterministic pagination with tied timestamps; filters; output and errors redacted.

### Endpoint 5 — Retrieve a run

- **Purpose:** poll status, safe result, decision, attempts, and audit summary.
- **Method/path:** `GET /api/v1/tool-runs/{run_id}`.
- **Auth/parameters/body:** owner required; UUID path; no query parameters or body.
- **Response:** `200 ToolRunDetail`; `safe_output` appears only when succeeded. Include attempt summaries but not worker secrets.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID; owner-scoped `404 TOOL_RUN_NOT_FOUND`.
- **Tables:** run, definition, decision, attempts, audit events.
- **Transaction/side effects:** consistent read; none.
- **Tests:** each state; other user's ID returns 404; terminal error bounded; secret-shaped fixture is redacted.

### Endpoint 6 — Decide an awaiting run

- **Purpose:** approve or reject the exact reviewed proposal.
- **Method/path:** `POST /api/v1/tool-runs/{run_id}/decisions`.
- **Auth:** owning human user plus recent-auth policy for destructive tools.
- **Parameters:** UUID path; no query parameters.
- **Body:** required non-null `decision: approved|rejected`; `tool_version` integer `>=1`; supported non-null ASCII `canonicalizer_version` 1–20 characters; and exactly 64 lowercase hexadecimal `input_hash`; optional nullable `reason` of 0–500 characters. Reject unknown fields.
- **Response:** `201 DecisionPublic`; embedded run status is `queued` or `rejected`.
- **Headers:** request ID and `Cache-Control: no-store`; no `Location`, because Phase 5 deliberately has no retrieve-decision endpoint and the decision is embedded by `GET /tool-runs/{id}`.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; `403 APPROVAL_FORBIDDEN`; `404 TOOL_RUN_NOT_FOUND`; `409 RUN_NOT_AWAITING_APPROVAL`; `409 DECISION_ALREADY_EXISTS`; `409 APPROVAL_BINDING_MISMATCH`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID/body.
- **Tables:** lock run; read definition; insert immutable decision and audit; update run.
- **Transaction:** one serial transaction with `SELECT ... FOR UPDATE`; recompute the binding from stored input and current referenced definition before insertion; one commit.
- **Side effects:** approval queues work but does not execute inline.
- **Tests:** approve/reject; duplicate/concurrent decisions yield one winner; changed version/hash rejected; model identity cannot approve; rejected never claimable; success has no `Location` header and the decision is visible through the run detail.

### Endpoint 7 — Request cancellation

- **Purpose:** prevent queued work from starting or ask a running handler to stop cooperatively.
- **Method/path:** `POST /api/v1/tool-runs/{run_id}/cancel`.
- **Auth:** owner; administrators need an explicit separate permission.
- **Parameters/body:** UUID path; body may be omitted or be exactly `{}` or `{"reason": string|null}` where a non-null reason is 0–500 characters; reject scalars, unknown fields, and longer values.
- **Response:** `200 ToolRunPublic`: queued becomes `cancelled_before_start`; running becomes `cancel_requested`; repeating is idempotent.
- **Headers:** request ID.
- **Errors/codes:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 TOOL_RUN_NOT_FOUND`; `409 RUN_TERMINAL` for a terminal run; `422 REQUEST_VALIDATION_FAILED` for malformed UUID/body/reason.
- **Tables:** lock/update run; append audit.
- **Transaction:** state check, update, event, and commit together.
- **Side effects:** a running executor may observe the flag; the response does not promise reversal.
- **Tests:** queued cancellation cannot be claimed; running transition; repeat; terminal conflict; race between claim and cancel has one legal result; other user hidden.

## 6. Vertical build slices

Build one complete path at a time.

1. **Registry-only slice:** code-register `list_tasks v1`, sync its safe definition, list/get it over HTTP, and test unknown handlers fail application startup or sync.
2. **Read-run slice:** create a queued run, claim it manually with a one-shot worker command, call `TasksService.list_for_user`, finalize, and poll the result.
3. **Approval slice:** add `create_task v1`; request -> await -> approve -> execute. Then reject a second run.
4. **Reliability slice:** add leases, attempt rows, heartbeat, retry classification, recovery, cancellation, and audit views.
5. **Model proposal slice:** only now let Phase 4 submit `requester=model`; it follows exactly the same validation and policy.

Keep commits small enough that each slice is runnable.

## 7. Limited reference snippets

The registry is executable configuration in code:

```python
from dataclasses import dataclass
from typing import Any, Protocol


class ToolHandler(Protocol):
    def __call__(self, *, user_id: str, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class RegisteredTool:
    handler_key: str
    version: int
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, tools: list[RegisteredTool]) -> None:
        self._tools = {(tool.handler_key, tool.version): tool for tool in tools}

    def resolve(self, handler_key: str, version: int) -> RegisteredTool:
        try:
            return self._tools[(handler_key, version)]
        except KeyError as exc:
            raise RuntimeError("database references an unknown tool handler") from exc
```

There is intentionally no `importlib`, `eval`, or string-to-function mechanism.

Use one versioned canonicalizer everywhere:

```python
import hashlib
import json
from typing import Any

CANONICALIZER_VERSION = "app-json-v1"


def canonical_bytes(value: Any) -> bytes:
    text = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return text.encode("utf-8")


def approval_hash(*, tool_name: str, tool_version: int, value: Any) -> str:
    binding = b"\x00".join(
        [tool_name.encode(), str(tool_version).encode(), canonical_bytes(value)]
    )
    return hashlib.sha256(binding).hexdigest()
```

This local format works only because input is validated into ordinary JSON values and the canonicalizer version is stored. Python's sorted JSON is not a complete cross-language canonicalization standard. If another language must reproduce hashes, adopt RFC 8785/JCS with official test vectors and migrate to a new version—never silently change v1.

The claim query must be short and transactional:

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session


def claim_one(session: Session, worker_id: str):
    now = datetime.now(timezone.utc)
    stmt = (
        select(ToolRun)
        .where(
            ToolRun.status == "queued",
            ToolRun.next_attempt_at <= now,
            ToolRun.attempt_count < ToolRun.max_attempts,
        )
        .order_by(ToolRun.next_attempt_at, ToolRun.created_at, ToolRun.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    run = session.scalar(stmt)
    if run is None:
        return None
    run.status = "running"
    run.lease_owner = worker_id
    run.lease_expires_at = now + timedelta(seconds=30)
    run.attempt_count += 1
    session.flush()
    return run
```

The caller inserts the attempt/audit and commits before executing. It copies only required scalar data, closes the claim session, runs the handler with a timeout, then opens a new session to finalize while checking the lease identity. Never hold a row lock or database transaction across slow or external handler execution.

For a quick same-PostgreSQL handler such as `create_task`, make task insertion, safe output, and run finalization one business transaction under the executor service. The Tasks domain service stages its change inside that caller-owned unit of work; it does not independently commit. A unique nullable `source_tool_run_id` on tasks adds defensive idempotency. If a tool calls an external system, that atomic transaction is impossible; use downstream idempotency/reconciliation and the uncertain-outcome rules instead.

Expired leases need a separate atomic recovery use case; the claim query must not silently steal `running` rows. In one short transaction, select one expired `running|cancel_requested` run and its latest attempt `FOR UPDATE SKIP LOCKED`, then mark that attempt `lease_expired`. With no handler start, use `cancelled_before_start` when cancellation was requested; otherwise requeue. After a start, a cancellation whose no-effect outcome is proven finishes `failed` with a safe cancellation code; an uncertain effect becomes `outcome_unknown`. Without cancellation, a registered `known_safe` read/same-database operation requeues only below max attempts, otherwise fails; a possibly non-idempotent external effect becomes `outcome_unknown`. Clear lease fields, set non-null `next_attempt_at`, append audit, and commit together. Run recovery before normal claims.

## 8. Retry, heartbeat, and failure policy

Classify failures explicitly:

- **Validation/permission/permanent domain failure:** terminal `failed`; retrying unchanged input cannot help.
- **Known transient internal failure:** in the finalization transaction, mark the attempt `retryable_failure`, transition `running -> queued`, clear the lease, and schedule non-null `next_attempt_at` with capped exponential backoff and jitter when attempts remain; otherwise transition to terminal `failed`.
- **Side effect known idempotent:** retry with the same downstream idempotency key.
- **Side effect may have happened and cannot be queried:** `outcome_unknown`, no automatic retry.
- **Worker disappeared before handler start:** expired lease can be safely requeued if the attempt recorded no start marker.
- **Worker disappeared after start:** use handler-specific reconciliation. Absence of a final DB write is not proof of absence at the target.

Heartbeat only long operations at a measured interval below lease duration. The finalizer must update using `WHERE id=:id AND lease_owner=:worker`; zero updated rows means it lost the lease and must not overwrite the newer owner.

## 9. Testing strategy

### Unit tests

- JSON Schema accepts/rejects exact examples and limits object depth/size.
- Canonical input is stable for reordered keys; NaN is rejected; a one-character change changes the hash.
- Risk policy sends reads directly to queued and writes/destructive actions to approval.
- Transition table rejects every illegal edge.
- Error classifier distinguishes retryable, permanent, and uncertain.
- Redactor removes known secret keys and truncates output.

Use fake repositories and a fake clock, but do not mock the state machine itself.

### PostgreSQL integration tests

- Constraints reject invalid state and lease pairs.
- Idempotency uniqueness survives two concurrent sessions.
- Two claimant threads/processes using `SKIP LOCKED` never claim the same row.
- An expired eligible lease is recovered; an active lease is not.
- Decision creation and state change are atomic.
- Owner-scoped queries never return another user's rows.
- Deleting a Phase 4 conversation with linked runs succeeds, sets every linked `tool_runs.conversation_id` to null through the database FK, and preserves the run, attempts, decisions, and audit events.
- Migration upgrade/downgrade/upgrade succeeds.

### Handler contract tests

Run every registered handler against a shared suite: validates declared input, respects the authenticated user, returns schema-valid bounded output, raises classified exceptions, and does not commit the caller's session unexpectedly. Use real internal services with a test database for `list_tasks`/`create_task`.

### API tests

Treat every bullet in the endpoint cards as a case. Assert status, body, headers, database state, and **absence** of a side effect after rejected/cancelled runs. Tests never call a model or paid provider.

## 10. Manual run and debugging

Start the API and a one-shot worker in separate terminals. Illustrative requests:

```bash
curl -i http://127.0.0.1:8000/api/v1/tools \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN"

curl -i -X POST http://127.0.0.1:8000/api/v1/tool-runs \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN" \
  -H "Idempotency-Key: tutorial-create-task-001" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"create_task","tool_version":1,"input":{"title":"Read worker docs"}}'
```

Use a task-specific environment variable, not a real token pasted into the guide. Retrieve the run, copy its returned binding fields, then post the decision.

When a run is stuck, ask in this order:

1. Is its status actually claimable and `next_attempt_at <= now()`?
2. Is there an active lease? Compare database time, not laptop assumptions.
3. Did a worker create an attempt and heartbeat?
4. Did schema validation or policy stop it before queueing?
5. Did the executor lose its lease before finalization?
6. Is the error retryable, permanent, or uncertain?
7. Do request ID, run ID, attempt ID, and worker ID connect the logs?

Log identifiers and transitions, never full inputs by default. A traceback belongs in protected logs; the API receives a safe code.

## 11. Security and reliability review

- Allowlisted handlers only; reject arbitrary commands, SQL, imports, and destinations.
- Owner scope every read and transition. A UUID is not authorization.
- Apply the same schema validation to model and human requests.
- Limit JSON byte size, nesting, string lengths, execution time, result size, attempts, and per-user queued work.
- Writes require human/policy approval; destructive operations require recent authentication and a narrow summary.
- Recompute approval binding from stored data inside the decision transaction.
- Encrypt any unavoidable tool credentials and keep keys outside PostgreSQL.
- Never log secrets, raw authorization headers, or confidential tool outputs.
- Use an outbound network allowlist for network-capable handlers and block private/link-local targets to reduce SSRF risk.
- Expose honest uncertain outcomes; never retry unknown writes automatically.
- Alert on lease recovery spikes, dead work, approval mismatch, repeated schema failures, and queue age.

## 12. Exercises and checkpoint

Exercises:

1. Write the complete transition table and a parameterized test for every allowed and forbidden edge.
2. Approve a run, alter a copy of its input, and prove the binding differs. Then prove the database row cannot be mutated through the API.
3. Start two workers with ten queued `list_tasks` runs and show every run has exactly one successful attempt.
4. Terminate one worker after claim. Advance the fake clock and recover the lease.
5. Create a handler that reports “provider accepted” and then raises a simulated connection loss. Explain why the correct status is `outcome_unknown`.
6. Add `result_truncated` and prove a megabyte result never reaches the API or logs.
7. Write an architecture decision record comparing fields on `tool_runs` with a generic `work_items` table. Do not refactor until another phase reuses the contract.

Checkpoint:

- Migrations rebuild the schema from zero.
- Manual read and write runs work; writes cannot bypass approval.
- Duplicate idempotency requests converge on one run.
- Two workers claim safely, heartbeats/leases recover, and attempts remain auditable.
- Rejection and pre-start cancellation produce no handler call.
- A running cancellation and outcome-unknown response never claim more certainty than exists.
- All unit, API, PostgreSQL concurrency, and handler contract tests pass.

## What you should be able to explain after Phase 5

Explain, without notes: why a database definition is not executable code; JSON Schema versus Pydantic; run versus attempt; service transaction ownership; `FOR UPDATE SKIP LOCKED`; lease and heartbeat; at-least-once versus exactly-once; idempotency identity; canonical input hashing; exact approval binding; direct read policy; rejection; pre-start versus running cancellation; outcome unknown; immutable audit events; and why an LLM is never an authorization boundary.

## Official primary documentation to read

Read with a question and record the answer in your learning journal:

- [PostgreSQL `SELECT`, row locking, and `SKIP LOCKED`](https://www.postgresql.org/docs/current/sql-select.html) — why PostgreSQL calls the view inconsistent and queue-like consumers appropriate.
- [SQLAlchemy `with_for_update(skip_locked=True)`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update) — how the ORM expresses the claim lock.
- [SQLAlchemy Session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) — where transactions begin, commit, rollback, and close.
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12) and [the specification index](https://json-schema.org/specification) — schemas, validation, and dialect identifiers.
- [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785) — why hashing JSON requires an invariant representation.
- [HTTP Semantics: `202 Accepted`](https://www.rfc-editor.org/rfc/rfc9110.html#name-202-accepted) — acceptance is not completion.
- [FastAPI dependency injection](https://fastapi.tiangolo.com/tutorial/dependencies/) — constructing services at the HTTP boundary.
- [Alembic autogenerate](https://alembic.sqlalchemy.org/en/latest/autogenerate.html) — why generated revisions must still be manually reviewed.

Stop here before Google integration. If you cannot prove a local write tool is controlled and recoverable, connecting Gmail or Calendar only magnifies the ambiguity.
