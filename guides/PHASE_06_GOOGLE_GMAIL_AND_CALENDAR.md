# Phase 6 — Google OAuth, Calendar, and Gmail

This phase links a user's Google account through delegated OAuth authorization, reads Calendar and Gmail through provider adapters, and performs controlled writes through the durable work substrate from Phase 5. Google authorization is **not** login to your own API: your Phase 2 access token still identifies the workspace user.

## Outcome, prerequisites, and non-goals

At the end you can:

- start and safely finish or reject a Google OAuth web-server flow;
- support one-time `state`, PKCE S256, exact redirect URIs, offline access, incremental/partial scopes, encrypted tokens, refresh, revocation, and disconnect;
- list Calendar events, queue an idempotent event creation, and update with an ETag precondition;
- list/get Gmail messages, create a MIME draft, and explicitly send one existing draft;
- run all automated tests against fakes, with live Google tests opt-in and manual.

Prerequisites: authentication/ownership, Phase 5 leases/retries/idempotency/audit, PostgreSQL/Alembic, and an HTTPS callback URL for any non-local environment. Create a dedicated Google Cloud project and test account; configure the consent screen, enable Calendar and Gmail APIs, and register an exact web application redirect URI.

Non-goals: “Sign in with Google” for your application, domain-wide delegation, mailbox mirroring, push sync/webhooks, attachment downloads, arbitrary Gmail modification, background email reading, and autonomous sending. Those increase data and policy scope before the core lifecycle is understood.

## 1. Concepts before code

OAuth has distinct roles:

- **resource owner:** the Google user;
- **client:** your backend, identified by client ID and (for a confidential web client) client secret;
- **authorization server:** Google endpoints that obtain consent and issue codes/tokens;
- **resource server:** Calendar and Gmail APIs;
- **redirect URI:** your fixed callback where Google returns a short-lived authorization code or error.

An authorization code is not an access token. The backend exchanges it, together with the exact redirect URI, client credentials, and PKCE verifier. An access token is short lived and authorizes only granted scopes. A refresh token can obtain later access tokens while the user is absent; treat it like a long-lived password.

`state` correlates one browser journey and prevents login/authorization CSRF. PKCE binds an intercepted code to the backend that created the verifier. They solve different problems, so use both. The state and verifier must be unpredictable and one-time.

A **scope** is a provider permission. Map application features to a server allowlist of exact Google scopes. Never accept a raw scope string from the frontend. Request only the next feature's permissions, store the actually granted set, and disable features whose scopes are missing. Partial consent is an expected business outcome, not a `500`.

An **adapter** translates your stable domain contract to Google's changing shapes. Routers and services work with `CalendarEvent`, not arbitrary provider dictionaries. Provider IDs remain opaque strings.

## 2. Architecture and flows

```text
browser -> authenticated API: create authorization request
API -> PostgreSQL: state hash + encrypted verifier + requested features + expiry
API -> browser: Google authorization URL
browser <-> Google: account selection and consent
Google -> API callback: code/state OR error/state
API -> PostgreSQL: atomically consume state
API -> Google token endpoint: code + PKCE verifier
API -> PostgreSQL: encrypted tokens + actual scopes + safe connection metadata
API -> browser: 303 to one fixed frontend result route
```

Provider access uses ports:

```text
Calendar router -> CalendarService -> CalendarPort -> GoogleCalendarAdapter -> Google
Email router    -> EmailService    -> EmailPort    -> GoogleGmailAdapter    -> Google
write service -> external_action_runs -> worker -> adapter -> finalized outcome
```

Reads may call Google during the request with a strict timeout because they have no external side effect. Writes are durable actions: persist and return `202`, then let a worker execute. Never keep a SQL transaction open across Google network I/O.

Recommended implementation order:

```text
connect -> Calendar list -> Calendar create -> Calendar update
        -> Gmail list/get -> Gmail draft -> explicit Gmail send
```

## 3. Exact dependencies, configuration, and folders

Move HTTPX from dev-only to runtime and add:

```toml
"httpx>=0.27,<1",
"cryptography>=50,<51",
"google-auth>=2.35,<3",
"email-validator>=2.2,<3",
```

HTTPX performs explicit REST calls and is easy to fake. `cryptography` encrypts stored credentials. `google-auth` validates Google identity-token claims if you request `openid email`; do not parse an ID token without signature, issuer, audience, expiry, and nonce validation. `email-validator` supports Pydantic `EmailStr`. You may later adopt Google's generated API client, but the port prevents that choice leaking into services.

Add non-secret names to `.env.example`:

```dotenv
APP_GOOGLE_CLIENT_ID=CHANGE_ME
APP_GOOGLE_CLIENT_SECRET=CHANGE_ME
APP_GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/v1/oauth/callbacks/google
APP_FRONTEND_INTEGRATION_RESULT_URL=http://127.0.0.1:3000/settings/integrations/google
APP_CREDENTIAL_KEYRING={"local-v1":"REPLACE_WITH_43_CHAR_UNPADDED_BASE64URL_KEY"}
APP_CREDENTIAL_ACTIVE_KEY_ID=local-v1
APP_OAUTH_BROWSER_BINDING_COOKIE=aiw_oauth_binding
APP_OAUTH_COOKIE_SECURE=false
```

The real values stay untracked. `APP_CREDENTIAL_KEYRING` is a JSON object from key ID to an **unpadded base64url value that decodes to exactly 32 random bytes**; the placeholder is intentionally invalid and startup must fail until it is replaced. `APP_CREDENTIAL_ACTIVE_KEY_ID` must name an entry. `APP_OAUTH_COOKIE_SECURE=false` is allowed only for exact localhost development; startup must require `true` elsewhere. In production, load client secret and encryption keys from a secret manager. Validate callback/frontend URLs at startup; never accept either from a request. If frontend and API origins differ, allow credentials only from the exact frontend origin—never wildcard CORS.

### The exact authenticated-encryption envelope

Use the maintained `cryptography` `AESGCM` recipe with a 256-bit key. Encryption without authentication is insufficient: an attacker who can alter database bytes must not be able to make the application decrypt a modified refresh token as if it were genuine.

The value stored in each credential `bytea` column is UTF-8 JSON with **exactly** these fields and no others:

```json
{"alg":"A256GCM","ciphertext":"<unpadded-base64url>","kid":"local-v1","nonce":"<16-char-unpadded-base64url>","v":1}
```

- `v` is integer `1`; `alg` is literal `A256GCM`.
- `kid` matches `^[a-z0-9][a-z0-9._-]{0,63}$` and selects a configured decrypt key. Mirror it in the existing key-ID column for rotation queries, but reject a mismatch rather than guessing.
- `nonce` decodes to exactly 12 bytes. Generate a fresh nonce with `secrets.token_bytes(12)` for **every** encryption. Never reuse a nonce with the same key, including when rewriting an unchanged token.
- `ciphertext` is `AESGCM.encrypt` output: encrypted bytes followed by its 16-byte authentication tag. It decodes to `len(plaintext) + 16` bytes. Bound the serialized envelope to 32,768 bytes before parsing and require plaintext length `1..16,384` bytes before encryption and after decryption.
- Serialization is canonical UTF-8 JSON (`sort_keys=True`, compact separators); base64url values have no `=` padding.

Authenticated additional data (AAD) prevents a valid ciphertext copied from one row or purpose from working elsewhere. Build AAD from server-owned values as canonical JSON with exactly `app`, `v`, `provider`, `table`, `row_id`, and `field`. Generate each UUID in the application before encryption so its final row identity is available. For example, a Google refresh token is bound to provider `google`, table `integration_connections`, its canonical lowercase UUID row ID, and field `refresh_token`. An OAuth PKCE verifier uses table `oauth_transactions` and field `pkce_verifier`. Phase 7 reuses the same primitive with provider `slack` and the appropriate row/field. Never take any of those labels from the client. Moving a secret to a new row/field, including disconnect transfer to `credential_revocations`, requires decrypting under the old AAD and immediately re-encrypting with a fresh nonce under the new AAD; copying ciphertext is deliberately invalid.

This limited reference implementation belongs in `token_crypto.py`:

```python
import base64
import binascii
import json
import re
import secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_KID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class CredentialEnvelopeError(Exception):
    """Safe internal category; never include secret input in its message."""


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64d(value: object) -> bytes:
    if not isinstance(value, str) or not _B64URL.fullmatch(value):
        raise CredentialEnvelopeError("invalid base64url field")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise CredentialEnvelopeError("invalid base64url field") from exc
    if _b64e(decoded) != value:
        raise CredentialEnvelopeError("non-canonical base64url field")
    return decoded


def credential_aad(
    *, provider: str, table: str, row_id: UUID, field: str
) -> bytes:
    allowed = {
        ("google", "integration_connections", "access_token"),
        ("google", "integration_connections", "refresh_token"),
        ("google", "oauth_transactions", "pkce_verifier"),
        ("google", "credential_revocations", "revocation_credential"),
        ("slack", "slack_installations", "access_token"),
        ("slack", "slack_installations", "incoming_webhook_url"),
        ("slack", "slack_oauth_transactions", "pkce_verifier"),
        ("slack", "credential_revocations", "revocation_credential"),
        ("internal", "notification_channels", "destination"),
        ("internal", "channel_verification_challenges", "email_code"),
    }
    if (provider, table, field) not in allowed:
        raise ValueError("unsupported credential context")
    value = {
        "app": "ai-workspace",
        "field": field,
        "provider": provider,
        "row_id": str(row_id),
        "table": table,
        "v": 1,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt_secret(
    plaintext: bytes, *, kid: str, key: bytes, aad: bytes
) -> bytes:
    if not _KID.fullmatch(kid) or len(key) != 32 or not 1 <= len(plaintext) <= 16_384:
        raise ValueError("invalid encryption input")
    nonce = secrets.token_bytes(12)
    envelope = {
        "alg": "A256GCM",
        "ciphertext": _b64e(AESGCM(key).encrypt(nonce, plaintext, aad)),
        "kid": kid,
        "nonce": _b64e(nonce),
        "v": 1,
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decrypt_secret(blob: bytes, *, keyring: dict[str, bytes], aad: bytes) -> tuple[bytes, str]:
    if len(blob) > 32_768:
        raise CredentialEnvelopeError("credential envelope too large")
    try:
        value = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialEnvelopeError("invalid credential envelope") from exc
    if not isinstance(value, dict) or set(value) != {"alg", "ciphertext", "kid", "nonce", "v"}:
        raise CredentialEnvelopeError("invalid credential envelope")
    kid = value["kid"]
    if (
        type(value["v"]) is not int
        or value["v"] != 1
        or value["alg"] != "A256GCM"
        or not isinstance(kid, str)
        or not _KID.fullmatch(kid)
    ):
        raise CredentialEnvelopeError("unsupported credential envelope")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if blob != canonical:
        raise CredentialEnvelopeError("non-canonical credential envelope")
    key = keyring.get(kid)
    if key is None or len(key) != 32:
        raise CredentialEnvelopeError("credential key unavailable")
    nonce = _b64d(value["nonce"])
    ciphertext = _b64d(value["ciphertext"])
    if len(nonce) != 12 or len(ciphertext) < 16:
        raise CredentialEnvelopeError("invalid credential envelope")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise CredentialEnvelopeError("credential authentication failed") from exc
    if not 1 <= len(plaintext) <= 16_384:
        raise CredentialEnvelopeError("credential plaintext too large")
    return plaintext, kid
```

Parse the keyring once at startup with the same strict decoder and fail closed if JSON, IDs, decoded lengths, or active-key membership is wrong. The service computes AAD from the locked row identity and expected field. Do not expose the exception text through an API; map an unavailable key to safe internal code `CREDENTIAL_KEY_UNAVAILABLE`, and an invalid tag/envelope to `CREDENTIAL_DECRYPTION_FAILED`. In either case, preserve ciphertext, skip the provider call, alert an operator, and do not silently mark the account revoked.

Rotation is read-old/write-new, not key replacement in place: decrypt with the envelope's `kid`; if it differs from the active key ID, encrypt with the active 32-byte key and a fresh nonce using the **same AAD**, then update ciphertext, mirrored key ID, and `credential_version` with an optimistic version predicate. A loser reloads instead of overwriting. A rotation worker handles dormant rows and Phase 7 secrets. Retain old decrypt keys until all live rows, queued revocations, replicas, and the accepted backup-retention window no longer require them. Removing a key first causes an avoidable outage.

Unit tests must cover: round trip; two encryptions of identical plaintext produce different nonces/ciphertexts; one-bit ciphertext/nonce/tag tampering; wrong row/field/provider AAD; missing/extra envelope keys; unknown key ID; malformed/oversize envelope; wrong key length; and plaintext bounds. An integration test inserts an old-key row, rotates it, proves the active `kid` and `credential_version` changed, proves plaintext did not, and races two rotators. A final test removes the old key only after rotation and still decrypts every live credential. The default suite uses generated test keys and never a real token.

Environment variables are an acceptable local-learning mechanism, not ideal production key custody. Use a secret manager or KMS-backed wrapping scheme in production, restrict key access separately from database access, rotate and audit access, and keep recoverable protected backups. Never commit keys, derive them from a human password, log the keyring/envelope/plaintext, or reuse one key for unrelated applications. AEAD at rest does not protect a compromised running process that can decrypt credentials, so least-privilege database/provider access and short-lived access tokens remain necessary.

Folder additions:

```text
app/
├── modules/integrations/
│   ├── model.py                 # connections and OAuth transactions
│   ├── schemas.py
│   ├── repository.py
│   ├── service.py               # lifecycle and scope checks
│   ├── token_crypto.py          # versioned encrypt/decrypt/key rotation
│   ├── router.py
│   └── dependencies.py
├── modules/external_actions/
│   ├── model.py                 # durable provider writes and attempts
│   ├── repository.py
│   ├── service.py
│   └── worker.py
├── modules/calendar/
│   ├── schemas.py
│   ├── ports.py
│   ├── service.py
│   └── router.py
├── modules/email/
│   ├── schemas.py
│   ├── mime.py                  # controlled EmailMessage construction
│   ├── ports.py
│   ├── service.py
│   └── router.py
└── providers/google/
    ├── oauth.py
    ├── http_client.py
    ├── calendar_adapter.py
    ├── gmail_adapter.py
    └── errors.py
tests/
├── unit/integrations/
├── unit/calendar/
├── unit/email/
├── integration/google/
├── contract/providers/
└── live/google/                 # excluded by default
```

Provider code depends inward on port/schema types. Domain modules do not import Google response classes.

## 4. Tables, constraints, and migration

### `integration_connections`

- `id uuid`, `user_id` FK, provider constrained to `google` for now.
- Verified provider identity: `provider_subject`, safe `account_email`; unique `(user_id, provider, provider_subject)`.
- `status`: `active|limited|reauthorization_required|revoked|disconnected`.
- `granted_scopes text[]` with a server-side canonical sorted representation, plus `enabled_features text[]` recording which application capabilities the user deliberately activated.
- encrypted access-token ciphertext/key ID and expiry; encrypted refresh-token ciphertext/key ID. Never expose these columns.
- `credential_version`, last successful refresh/use, safe error code, timestamps.

Add an owner/status index. If one workspace user may connect the same Google account only once, the unique constraint enforces it. Do not make email the durable provider identity; addresses can change. Use a validated `sub` claim.

### `oauth_transactions`

- `id`, `user_id`, provider, `state_hash char(64)` unique.
- encrypted PKCE verifier plus key ID; `code_challenge`, mandatory `nonce_hash` for OIDC identity claims, and `browser_binding_hash` for the initiating HttpOnly cookie.
- requested feature names and derived scopes; fixed `return_route` enum/key—not an arbitrary URL.
- `expires_at`, `consumed_at`, and outcome `pending|authorized|denied|failed|expired` with safe error code.

Store only the state hash; the random state travels through the browser. The verifier must be recoverable for exchange, so encrypt rather than hash it. Add a partial unique index allowing one unconsumed Google transaction per user in this baseline, plus an expiry cleanup index. Delete expired transaction secrets on a short retention schedule.

### `external_action_runs`

- `id`, `user_id`, `connection_id`, operation constrained to `calendar.create|calendar.update|gmail.draft.create|gmail.draft.send`.
- canonical validated input, input hash, idempotency key; unique `(user_id, operation, idempotency_key)`.
- optional provider resource ID, provider request ID, expected/provider-result ETag, and safe resource content hash for app-created Gmail drafts.
- `queued|running|succeeded|failed|outcome_unknown|cancelled_before_start|cancel_requested`, including nullable `provider_dispatch_started_at` set in the final status recheck transaction.
- all Phase 5 work fields: attempts, next attempt, max attempts, lease owner/expiry, heartbeat, cancel/start/finish times, safe error.

Create `external_action_attempts` using the Phase 5 attempt contract. An action is user-visible; attempts are worker history.

### `credential_revocations`

- `id`, connection/user/provider, encrypted one-use revocation credential plus key ID, and `queued|running|succeeded|failed|dead_letter`.
- the Phase 5 attempt/lease/retry fields, with non-null `next_attempt_at`, safe error, and timestamps.

The revocation row temporarily owns the encrypted token after local disconnect. Its worker wipes ciphertext on success, provider `invalid_token` (already revoked), or a documented terminal outcome; transient failures retry durably and dead-letter alerts require operator cleanup.

Write the migration manually enough to understand it. Inspect encrypted columns as `bytea`, array defaults, FKs, unique keys, state checks, lease checks, queue indexes, and downgrade order. No ORM event or startup `create_all()` substitutes for Alembic.

## 5. OAuth lifecycle in detail

### Start

The authenticated user requests feature names such as `calendar_read` or `gmail_compose`. The service maps them to an allowlisted scope set and adds the configured `openid email` identity scopes. It generates at least 32 random bytes each for state, OIDC nonce, and browser binding plus a 43–128 character PKCE verifier. Store only hashes of state, nonce, and binding; encrypt the verifier. Support one active Google flow per user/browser in the baseline and persist it before returning a URL.

The Google URL uses `response_type=code`, configured client ID/redirect URI, the **raw nonce**, state, `code_challenge_method=S256`, requested scopes, `access_type=offline`, and `include_granted_scopes=true`. The raw nonce goes only to Google while its digest stays in PostgreSQL. Return an HttpOnly, SameSite=Lax browser-binding cookie with `Path=/api/v1/oauth/callbacks/google`; Secure is mandatory outside localhost. Clear it later with the same name, path, domain policy, and security attributes. Do not force `prompt=consent` on every connection.

### Callback success, denial, and replay

The callback is a browser endpoint, not a normal bearer-authenticated API call. Its state maps back to the authenticated user who started the flow, while the HttpOnly binding proves the same browser completed it. In one short transaction, hash both values, lock the matching unconsumed/unexpired row, validate both, mark it consumed, and commit. A missing/mismatched binding, unknown/expired/replayed state, or wrong provider causes no token call. Clear the binding cookie on every terminal callback response.

If Google returned `error`, store a bounded internal outcome such as `denied`; never log or reflect raw query values. If it returned a code, decrypt the verifier and exchange outside the database transaction. A code is secret and short lived: never log or persist it. A transient exchange failure starts a fresh authorization journey rather than replaying a consumed callback.

Validate token/identity response fields, including signature, issuer, audience, expiry, and exact nonce. Determine actual granted scopes. Enable only requested application features whose required scopes were actually granted, then upsert the connection in a new transaction. Encrypt tokens before persistence. Redirect with `303 See Other` to one configured frontend URL containing only a local transaction ID/result code—not tokens, codes, state, email, or raw provider error.

### Refresh-token preservation and rotation

Google may return a refresh token only on an earlier grant. For the same validated provider identity:

```text
new refresh token present -> encrypt and replace, increment credential_version
new refresh token absent + old valid token exists -> preserve old ciphertext exactly
new refresh token absent + no old token -> mark reauthorization_required/offline_unavailable
```

Never assign `connection.refresh_token = token_response.get("refresh_token")`; that can erase the only durable credential with `null`.

Refresh access tokens outside a SQL transaction. Read a credential version, decrypt, call Google with a timeout, then update with an optimistic `WHERE credential_version = :observed`. If another worker won, discard your access token and reload. On `invalid_grant`, mark reauthorization required with a safe code and stop retrying. Preserve a rotated refresh token only when explicitly present.

## 6. Endpoint contract worksheets

Client JSON schemas use `ConfigDict(extra="forbid")`; unknown fields return `422`, and `null` is rejected except for fields explicitly identified as clearable. JSON request bodies are at most 65,536 bytes except Gmail draft creation, which is at most 131,072 bytes; calendar/provider IDs are 1–1,024 characters and reject control characters, NUL, or a decoded `/` in one path segment. An authenticated endpoint's raw query component is at most 8,192 bytes and 30 pairs. Every opaque cursor is 1–4,096 unpadded base64url ASCII characters and decodes to at most 3,072 bytes of UTF-8 JSON containing exactly version, provider page token, filter fingerprint, connection ID, and expiry; it is server-signed. Non-string, empty, overlong, padded, or non-base64url cursor input is `422 REQUEST_VALIDATION_FAILED`; after that surface check, invalid UTF-8/JSON/signature, unknown/missing keys, unsupported version, expiry, malformed semantic values, trailing data, or filter/connection mismatch is `400 INVALID_CURSOR`. Every response includes `X-Request-ID`; authenticated data uses `Cache-Control: no-store`. Errors use the common envelope. `connection_id` is always owner-scoped. Provider payloads are normalized, bounded, and HTML is never trusted.

Every write endpoint's `Idempotency-Key` is 8–200 characters each in non-space printable ASCII `0x21..0x7E`; do not trim or normalize it. Missing, duplicate, control, non-ASCII, shorter, or longer values return `422 IDEMPOTENCY_KEY_INVALID`. `If-Match` is exactly one header value of 1–1,024 visible ASCII characters `0x21..0x7E`, retained byte-for-byte; missing/duplicate/invalid returns `422 IF_MATCH_INVALID`. A surfaced provider retry delay is an integer `Retry-After` clamped to 1–3,600 seconds; missing/malformed provider values use the operation's configured backoff. Tests cover all header boundaries and clamp behavior.

For the Google callback, reject a request-target query component over 4,096 bytes **before** parsing. Accept these known fields only: `state` 43–256 URL-safe characters; `code` 1–1,024 characters; `error` 1–100 ASCII `[A-Za-z0-9_.-]`; `error_description` 0–500 characters; `error_uri` 0–1,024 characters; and `scope` 0–4,096 characters. At most 12 query pairs and one value per key are allowed; duplicate known keys, invalid UTF-8/percent encoding, control characters, or simultaneous `code` and `error` return `400 OAUTH_RESPONSE_INVALID`. Up to eight unknown keys are ignored for provider forward compatibility only when each key is 1–64 ASCII `[A-Za-z0-9_.-]` and each value is at most 256 characters; more or larger unknown fields return `400 OAUTH_RESPONSE_INVALID`. Never log, store, or reflect unknowns, code, descriptions, or URI. The Slack callback in Phase 7 uses these same numeric limits.

### Normative response vocabulary

Timestamps are UTC RFC 3339 strings with `Z`. All listed keys are present unless the type says optional; only `| null` fields may be null. No secret, provider raw response, token, ciphertext, or key ID appears.

- **`IntegrationPublic`:** `id: UUID`; `provider: "google"`; `account_email: EmailStr`; `status: "active"|"limited"|"reauthorization_required"|"revoked"|"disconnected"`; `enabled_features: array["calendar_read"|"calendar_write"|"gmail_read"|"gmail_compose"|"gmail_send"]` sorted and unique; `reauthorization_required: boolean`; `created_at: datetime`; `updated_at: datetime`.
- **`AuthorizationRequestPublic`:** `authorization_request_id: UUID`; `authorization_url: HTTPS URL` whose host is exactly `accounts.google.com`; `expires_at: datetime`. It contains the raw one-use state and nonce only inside the URL query; neither value is repeated in another response field.
- **`CalendarEventTimePublic`:** exactly one of `date_time: datetime` or `date: YYYY-MM-DD` is non-null; `time_zone: IANA zone string | null` (required for timed events, null for all-day events).
- **`CalendarPersonPublic`:** `email: EmailStr`; `display_name: string of 0–200 characters | null`; `response_status: "needsAction"|"declined"|"tentative"|"accepted"|null`; `self: boolean`.
- **`CalendarEventPublic`:** `id: string` 1–1,024; `etag: string` 1–1,024; `calendar_id: string` 1–1,024; `status: "confirmed"|"tentative"|"cancelled"`; `summary: string` 0–1,000; `description: string up to 8,000 | null`; `location: string up to 1,000 | null`; `start: CalendarEventTimePublic`; `end: CalendarEventTimePublic`; `organizer: CalendarPersonPublic | null`; `attendees: array[CalendarPersonPublic]` capped at 100; `updated_at: datetime`.
- **`EmailMessageSummaryPublic`:** `id: string` 1–1,024; `thread_id: string` 1–1,024.
- **`EmailHeaderPublic`:** `name: "From"|"To"|"Cc"|"Date"|"Subject"|"Message-ID"|"In-Reply-To"|"References"`; `value: string` at most 8,192 characters. Missing provider headers are omitted from the array; no raw header block is exposed.
- **`EmailAttachmentMetadataPublic`:** `filename: string` 0–255; `media_type: string` 1–255; `size_bytes: integer >= 0`; `attachment_id: string` 1–1,024. This phase has no bytes/download URL.
- **`EmailMessagePublic`:** `id: string`; `thread_id: string`; `label_ids: array[string]` capped at 100; `headers: array[EmailHeaderPublic]` capped at 50; `plain_text_body: string up to 1,048,576 characters | null`; `attachments: array[EmailAttachmentMetadataPublic]` capped at 100; `format: "metadata"|"full"`. Metadata format always returns null body.
- **`ExternalActionErrorPublic`:** `code: string` 1–80 ASCII; `message: string` 1–500; `details: object | null` at most 8,192 canonical bytes. It contains no raw provider response.
- **`ExternalActionAttemptPublic`:** `attempt_number: integer >= 1`; `outcome: "executing"|"succeeded"|"retryable_failure"|"permanent_failure"|"lease_expired"|"outcome_unknown"`; `started_at: datetime`; `finished_at: datetime | null`; `duration_ms: integer >= 0 | null`; `provider_request_id: string 1–255 | null`; `safe_error: ExternalActionErrorPublic | null`.
- **`ExternalActionPublic`:** `id: UUID`; `connection_id: UUID`; `operation: "calendar.create"|"calendar.update"|"gmail.draft.create"|"gmail.draft.send"`; `status: "queued"|"running"|"cancel_requested"|"cancelled_before_start"|"succeeded"|"failed"|"outcome_unknown"`; `input_hash: 64 lowercase hex`; `attempt_count: integer >= 0`; `max_attempts: integer >= 1`; `next_attempt_at: datetime` (always present; status determines claimability); `provider_resource_id: string 1–1,024 | null` (success only); `provider_etag: string 1–1,024 | null`; `resource_content_hash: 64 lowercase hex | null` (successful app draft only); `safe_error: ExternalActionErrorPublic | null`; `created_at: datetime`; `started_at: datetime | null`; `finished_at: datetime | null`; `status_url: string` equal to `/api/v1/external-actions/{id}`; `attempts: array[ExternalActionAttemptPublic] | null`. Queue responses set `attempts=null`; Endpoint 12 returns the array ordered by attempt number.

List envelopes are exactly `{ "items": array[T], "next_cursor": string | null }`.

### Normative Phase 6 error vocabulary

Every error response uses the common envelope and exactly one stable status/code below. Common `401 INVALID_ACCESS_TOKEN`, `422 REQUEST_VALIDATION_FAILED`, and `500 INTERNAL_SERVER_ERROR` retain their earlier meanings. Generic validation covers malformed UUID/JSON, unknown fields, wrong types, invalid body lengths/enums/time zones/addresses, path-provider IDs, and transport limits unless a narrower code applies.

| Status and code | Exact condition |
|---|---|
| `400 OAUTH_RESPONSE_INVALID` | Callback query violates its exact shape/encoding or lacks the required success/error alternative. |
| `400 OAUTH_STATE_INVALID` | State is unknown, already consumed/replayed, or for the wrong provider. |
| `400 OAUTH_STATE_EXPIRED` | Matching state exists but is expired. |
| `400 OAUTH_BROWSER_BINDING_INVALID` | Initiating browser cookie is missing or does not match. |
| `401 INVALID_ACCESS_TOKEN` | Missing or invalid application bearer credential. |
| `403 FEATURE_FORBIDDEN` | Authenticated user may not enable/use the requested application feature. |
| `403 RECENT_AUTH_REQUIRED` | Sensitive disconnect/send-scope/send operation lacks recent local authentication. |
| `403 INTEGRATION_SCOPE_MISSING` | Connection's actual granted scopes do not authorize the operation. |
| `404 INTEGRATION_NOT_FOUND` | Connection is absent or belongs to another user. |
| `404 EMAIL_MESSAGE_NOT_FOUND` | Google definitively reports no such message for the owned connection. |
| `404 APP_DRAFT_NOT_FOUND` | Draft ID has no succeeded app-owned draft record for this user/connection. |
| `404 EXTERNAL_ACTION_NOT_FOUND` | Action is absent or belongs to another user. |
| `409 INTEGRATION_REAUTH_REQUIRED` | Connection is revoked/limited such that renewed consent is required. |
| `409 IDEMPOTENCY_KEY_REUSED` | Same scoped key is bound to different canonical input. |
| `409 DRAFT_CONTENT_HASH_MISMATCH` | Submitted expected hash differs from the app-owned draft record. |
| `409 DRAFT_ALREADY_SENT` | App record already proves this logical draft was sent. |
| `413 MESSAGE_TOO_LARGE` | Selected normalized Gmail body exceeds the 1 MiB full-format limit. |
| `422 REQUEST_VALIDATION_FAILED` | Generic path/query/body schema failure described above. |
| `400 INVALID_CURSOR` | Surface-valid cursor fails semantic decode/signature/schema/binding/version/expiry validation. |
| `422 PAGE_LIMIT_INVALID` | `limit` is not an integer in `1..100`. |
| `422 INTEGRATION_FILTER_INVALID` | Provider/status list filter is not in its closed enum. |
| `422 CALENDAR_QUERY_INVALID` | Calendar ID/time range/window/filter is invalid. |
| `422 EMAIL_QUERY_INVALID` | Gmail query/include/format parameter is invalid. |
| `422 EMPTY_UPDATE` | Calendar PATCH has no resource change. |
| `422 IDEMPOTENCY_KEY_INVALID` | Required key violates the exact header policy above. |
| `422 IF_MATCH_INVALID` | Required Calendar ETag header violates the exact header policy above. |
| `422 DRAFT_CONFIRMATION_INVALID` | Send confirmation literal/hash shape is malformed. |
| `429 AUTHORIZATION_RATE_LIMITED` | Local OAuth-start rate limit is exceeded. |
| `429 EXTERNAL_ACTION_QUOTA_EXCEEDED` | Local queued-write/user budget is exceeded. |
| `429 PROVIDER_RATE_LIMITED` | Google read request is rate-limited; response carries bounded `Retry-After`. |
| `502 PROVIDER_RESPONSE_INVALID` | Provider returns an unusable/malformed success representation. |
| `503 PROVIDER_UNAVAILABLE` | Provider has a transient availability/5xx failure. |
| `504 PROVIDER_TIMEOUT` | Configured provider request deadline expires. |
| `500 INTERNAL_SERVER_ERROR` | Unclassified server defect; response is sanitized. |

Provider failures encountered later by a durable write worker become the action's safe terminal/retry code (for example `EVENT_VERSION_CONFLICT`) rather than retroactively changing the queue endpoint's `202` response. Framework `405` and sanitized `500` remain common outcomes rather than repeated per card.

### Endpoint 1 — Create Google authorization request

- **Purpose:** start one incremental, offline OAuth journey.
- **Method/path:** `POST /api/v1/integrations/google/authorization-requests`.
- **Auth:** current workspace user required; recent auth for adding send scope.
- **Params/headers:** none beyond bearer and request ID.
- **Body:** `{"features":["calendar_read"],"return_route":"integration_settings"}`; `features` contains 1–5 unique values from exactly `calendar_read|calendar_write|gmail_read|gmail_compose|gmail_send`; the service expands prerequisites but never accepts raw scopes. In this baseline `return_route` is the single literal `integration_settings`, not a URL. Both fields are required/non-null; reject duplicates, unknown values, unknown fields, empty lists, and lists longer than five.
- **Response:** `201 AuthorizationRequestPublic`.
- **Headers:** request ID, `Cache-Control: no-store`, and a random `Set-Cookie` binding with HttpOnly, SameSite=Lax, `Path=/api/v1/oauth/callbacks/google`, and environment-correct Secure; no `Location` because this baseline has no retrieval endpoint.
- **Errors/status:** `401 INVALID_ACCESS_TOKEN`; `403 FEATURE_FORBIDDEN`; `403 RECENT_AUTH_REQUIRED` when send scope is requested without recent auth; `422 REQUEST_VALIDATION_FAILED`; `429 AUTHORIZATION_RATE_LIMITED`.
- **Tables:** read the existing connection/scopes/features and insert the OAuth transaction.
- **Transaction:** generate secrets, persist encrypted verifier/state hash, commit, then return URL.
- **Side effects:** no Google call and no permission yet.
- **Tests:** feature list lengths 0/1/5/6, duplicate/unknown features, raw scopes/return URLs/wrong return-route literal rejected; state/verifier/nonce/binding entropy/expiry; only digests/ciphertext stored; raw nonce is in the Google URL; cookie attributes; send scope recent-auth; two requests differ.

### Endpoint 2 — Google OAuth callback

- **Purpose:** consume provider success or denial and finish connection safely.
- **Method/path:** `GET /api/v1/oauth/callbacks/google`.
- **Auth:** no bearer; valid one-time state **and** initiating HttpOnly browser-binding cookie are required.
- **Params:** no path parameters; the query must satisfy the exact 4,096-byte/pair/field limits above, with `state` required and exactly one of `code` or provider `error`. No request body.
- **Body:** none.
- **Response:** `303 See Other` to the fixed frontend result URL.
- **Headers:** `Location: {APP_FRONTEND_INTEGRATION_RESULT_URL}?transaction_id=<local-UUID>&result=<connected|limited|denied|failed>` using normal URL encoding and no other query/fragment; `Cache-Control: no-store`; `Referrer-Policy: no-referrer`; binding cookie cleared with `Max-Age=0` and the exact attributes/path used when setting it.
- **Errors/status:** `400 OAUTH_RESPONSE_INVALID`; `400 OAUTH_STATE_INVALID`; `400 OAUTH_STATE_EXPIRED`; `400 OAUTH_BROWSER_BINDING_INVALID`. User denial and a handled token-exchange failure use the documented `303` result `denied` or `failed`, respectively; never reflect provider text.
- **Tables:** lock/consume OAuth transaction; upsert connection on success.
- **Transaction:** consume state atomically first; token network exchange outside transaction; second transaction stores encrypted result. State remains consumed if exchange fails.
- **Side effects:** one token exchange on valid success; none on error/replay.
- **Tests:** happy path; denial; missing code; error+code; missing/wrong binding; expired/unknown/replayed state; PKCE verifier and raw nonce validation; fixed redirect/cookie clearing; token/code absent from logs/URL; partial scopes; refresh preservation.

### Endpoint 3 — List integrations

- **Purpose:** show safe account/capability metadata.
- **Method/path:** `GET /api/v1/integrations`.
- **Auth:** required.
- **Params:** optional non-null `provider`, whose only current value is `google`; optional non-null status from `active|limited|reauthorization_required|revoked|disconnected`; `limit` integer default 20, range 1–100; optional non-null 1–4,096-character cursor satisfying the phase policy and bound to both filters and `(created_at DESC,id DESC)`.
- **Body:** none.
- **Response:** `200 {"items": array[IntegrationPublic], "next_cursor": string | null}`. Raw provider scopes stay out of the ordinary UI contract; keep them available only to a privileged diagnostic view.
- **Headers:** request ID, `no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 INTEGRATION_FILTER_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor semantic/signature/filter mismatch.
- **Tables/transaction/side effects:** owner-scoped connection read; read-only; none.
- **Tests:** isolation; no ciphertext/token/key ID; partial features; pagination.

### Endpoint 4 — Disconnect an integration

- **Purpose:** stop local use immediately, remove connection credentials, and durably attempt Google revocation.
- **Method/path:** `DELETE /api/v1/integrations/{connection_id}`.
- **Auth:** owner and recent authentication.
- **Params/body:** UUID path; no query parameters or body.
- **Response:** `204 No Content`.
- **Headers:** request ID.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 RECENT_AUTH_REQUIRED`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID; owner-scoped `404 INTEGRATION_NOT_FOUND`.
- **Tables:** lock connection; generate the revocation UUID, decrypt the chosen revocation token with connection-row AAD, re-encrypt it with a fresh nonce and the revocation-row AAD, insert/reuse credential-revocation work, set disconnected/erase connection ciphertext, cancel queued actions/request cancellation of dispatched work, and audit.
- **Transaction:** all local disablement and durable revocation intent commit once. If already disconnected, return `204` without another job. Never place token plaintext in the job payload/audit.
- **Side effects:** none inline. A revocation worker calls Google later and wipes its token copy. Provider-action workers lock and recheck connection status immediately before atomically recording `provider_dispatch_started_at`; if disconnect committed first they cannot dispatch. If dispatch recorded first, disconnect cannot promise reversal and marks it cancel-requested/observable.
- **Tests:** other user hidden; token exists only as revocation ciphertext after commit; provider outage/crash recovers job; repeat `204`; queued work blocked; serialized disconnect-vs-dispatch race has one of the two documented outcomes; action worker rechecks status.

### Endpoint 5 — List Calendar events

- **Purpose:** read a bounded time window through the selected account.
- **Method/path:** `GET /api/v1/calendar/events`.
- **Auth:** required with `calendar_read` feature/scope.
- **Params:** required non-null UUID `connection_id`; optional non-null `calendar_id` 1–1,024 characters default `primary`; required non-null offset-aware RFC 3339 `time_min`/`time_max` with `time_min < time_max` and a window at most 31 days; integer `limit` default 50 and range 1–100; optional non-null 1–4,096-character signed cursor satisfying the phase policy and bound to connection, calendar, both times, limit-independent page order, and provider page token.
- **Body:** none.
- **Response:** `200 {"items": array[CalendarEventPublic], "next_cursor": string | null}`. The exact bounded organizer/attendee exposure is the normative vocabulary above; do not pass through additional provider people fields.
- **Headers:** request ID, `no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 INTEGRATION_SCOPE_MISSING`; owner-scoped `404 INTEGRATION_NOT_FOUND`; `409 INTEGRATION_REAUTH_REQUIRED`; `422 CALENDAR_QUERY_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor semantic/signature/filter mismatch; `429 PROVIDER_RATE_LIMITED` with bounded `Retry-After`; `502 PROVIDER_RESPONSE_INVALID`; `503 PROVIDER_UNAVAILABLE`; `504 PROVIDER_TIMEOUT`.
- **Tables:** read connection/token metadata; no event persistence.
- **Transaction:** load encrypted credential/version and close before HTTP. If refresh occurs, persist its encrypted access token/expiry with the documented credential-version compare-and-set transaction; otherwise perform no token write.
- **Side effects:** Google read only.
- **Tests:** fake pagination; all-day/dateTime normalization; partial scope; revoked token; timeout/429 mapping; page token never trusted across connection/filter changes.

### Endpoint 6 — Queue Calendar event creation

- **Purpose:** explicitly create one event as durable work.
- **Method/path:** `POST /api/v1/calendar/events`.
- **Auth:** owner with calendar write scope. A model cannot call as the user; it proposes a Phase 5 tool run.
- **Params/headers:** no path/query parameters; exactly one required `Idempotency-Key` satisfying the phase header policy.
- **Body:** required non-null `connection_id: UUID`; optional non-null `calendar_id` 1–1,024 characters default `primary`; required trimmed `summary` 1–1,000; optional nullable `description` at most 8,000 and `location` at most 1,000. `start` and `end` are required and use the same form: either `{date_time: AwareDatetime,time_zone: valid IANA string 1..255}` or all-day `{date: YYYY-MM-DD}`; timed and all-day forms cannot mix, start is strictly before end, and duration is at most 366 days. `attendees` is an optional default-empty list of 0–100 unique `EmailStr` values. `send_updates` is optional non-null `none|all|externalOnly`, default `none`. Reject null elsewhere, unknown/server-owned/provider-result fields, duplicate attendees, and mixed/empty time objects.
- **Response:** `202 ExternalActionPublic` with status URL.
- **Headers:** `Location: /api/v1/external-actions/{id}`, request ID.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 INTEGRATION_SCOPE_MISSING`; `404 INTEGRATION_NOT_FOUND`; `409 IDEMPOTENCY_KEY_REUSED`; `409 INTEGRATION_REAUTH_REQUIRED`; `422 IDEMPOTENCY_KEY_INVALID`; `422 REQUEST_VALIDATION_FAILED` for event/time/zone/attendee/body failures; `429 EXTERNAL_ACTION_QUOTA_EXCEEDED`.
- **Tables:** insert external action+audit; read connection.
- **Transaction:** input/hash/action/audit commit once; no Google call.
- **Side effects:** worker later uses a deterministic provider-valid event ID derived from action identity to prevent duplicate inserts.
- **Tests:** every length/list/duration boundary; timed/all-day/mixed forms and DST offsets/zones; same key converges; changed body conflicts; attendee uniqueness and notification choice; fake provider receives normalized request once; crash/retry yields one provider event.

### Endpoint 7 — Queue Calendar partial update

- **Purpose:** update specified fields without silently overwriting concurrent Google changes.
- **Method/path:** `PATCH /api/v1/calendar/events/{provider_event_id}`.
- **Auth:** owner with write scope.
- **Params/headers:** required non-null UUID query `connection_id`; optional non-null query `calendar_id` 1–1,024 default `primary`; opaque `provider_event_id` path 1–1,024 under the phase path policy; exactly one required `If-Match` and one required `Idempotency-Key`, each satisfying the phase header policy; no other query parameters.
- **Body:** require at least one resource change from `summary` (non-null trimmed 1–1,000), nullable `description` (max 8,000), nullable `location` (max 1,000), `attendees` (non-null 0–100 unique `EmailStr`), or a **paired** non-null `start` and `end` using the exact create-time schema/rules. `send_updates: none|all|externalOnly` may accompany a change and defaults to `none`, but is not a change by itself. Omitted means unchanged; explicit null clears only description/location. Reject unknown/provider-owned fields, a lone start/end, and empty/no-op patches.
- **Response:** `202 ExternalActionPublic`.
- **Headers:** `Location: /api/v1/external-actions/{id}`, request ID.
- **Errors:** queue-time `401 INVALID_ACCESS_TOKEN`; `403 INTEGRATION_SCOPE_MISSING`; owner-scoped `404 INTEGRATION_NOT_FOUND`; `409 IDEMPOTENCY_KEY_REUSED`; `409 INTEGRATION_REAUTH_REQUIRED`; `422 IDEMPOTENCY_KEY_INVALID`; `422 IF_MATCH_INVALID`; `422 EMPTY_UPDATE`; `422 REQUEST_VALIDATION_FAILED` for other patch/path/query failures; `429 EXTERNAL_ACTION_QUOTA_EXCEEDED`. A provider `412` occurs later and finalizes the action with safe code `EVENT_VERSION_CONFLICT`; this asynchronous endpoint itself already returned `202`.
- **Tables:** durable action/audit; connection.
- **Transaction:** enqueue only; expected ETag is part of canonical input/hash.
- **Side effects:** worker sends provider patch with `If-Match`; never automatically retries a 412 as a blind overwrite.
- **Tests:** missing ETag; null versus omitted; stale provider ETag -> terminal failed action with `EVENT_VERSION_CONFLICT`; same action retry; provider ID remains opaque and encoded safely.

### Endpoint 8 — List Gmail messages

- **Purpose:** search/list safe summaries on demand.
- **Method/path:** `GET /api/v1/email/messages`.
- **Auth:** owner with Gmail read scope.
- **Params:** required non-null UUID `connection_id`; optional non-null Gmail `q` of 1–500 characters; integer `limit` default 25 and range 1–100; optional non-null 1–4,096-character signed cursor satisfying the phase policy and bound to connection/query/include flag/provider order; optional non-null `include_spam_trash` is exact lowercase `true|false`, default `false`.
- **Body:** none.
- **Response:** `200 {"items": array[EmailMessageSummaryPublic], "next_cursor": string | null}`. Keep list sparse as Google's method is sparse; clients retrieve selected message metadata with Endpoint 9 instead of causing hidden N+1 provider calls.
- **Headers:** request ID, `no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 INTEGRATION_SCOPE_MISSING`; `404 INTEGRATION_NOT_FOUND`; `409 INTEGRATION_REAUTH_REQUIRED`; `422 EMAIL_QUERY_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor semantic/signature/filter mismatch; `429 PROVIDER_RATE_LIMITED` with bounded `Retry-After`; `502 PROVIDER_RESPONSE_INVALID`; `503 PROVIDER_UNAVAILABLE`; `504 PROVIDER_TIMEOUT`.
- **Tables/transaction:** connection read only; no mailbox mirror; close before provider call.
- **Side effects:** provider reads only.
- **Tests:** query encoding, pagination, malicious snippets rendered as data, partial scope/revocation, no message persistence.

### Endpoint 9 — Retrieve a Gmail message

- **Purpose:** normalize one owned account's message safely.
- **Method/path:** `GET /api/v1/email/messages/{provider_message_id}`.
- **Auth:** owner with Gmail read scope.
- **Params:** required UUID `connection_id`; opaque `provider_message_id` path 1–1,024 characters; optional non-null format `metadata|full`, default `metadata`. No raw format initially.
- **Body:** none.
- **Response:** `200 EmailMessagePublic` with From/To/Cc/Date/Subject/Message-ID/In-Reply-To/References and provider/thread IDs; `format=metadata` omits body, while `format=full` includes at most 1 MiB normalized plain text plus attachment metadata but no attachment bytes.
- **Headers:** request ID, `no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 INTEGRATION_SCOPE_MISSING`; owner-scoped `404 INTEGRATION_NOT_FOUND`; provider `404 EMAIL_MESSAGE_NOT_FOUND`; `409 INTEGRATION_REAUTH_REQUIRED`; `413 MESSAGE_TOO_LARGE`; `422 EMAIL_QUERY_INVALID`; `422 REQUEST_VALIDATION_FAILED` for malformed path/connection; `429 PROVIDER_RATE_LIMITED` with bounded `Retry-After`; `502 PROVIDER_RESPONSE_INVALID`; `503 PROVIDER_UNAVAILABLE`; `504 PROVIDER_TIMEOUT`.
- **Tables/transaction/side effects:** connection read, provider read, no body persistence.
- **Tests:** multipart/alternative, nested MIME, missing text, huge part, malformed headers, HTML sanitized/not executed, provider errors and user isolation.

### Endpoint 10 — Queue Gmail draft creation

- **Purpose:** create an unsent new/reply draft with explicit recipients/content.
- **Method/path:** `POST /api/v1/email/drafts`.
- **Auth:** owner with compose scope.
- **Params/headers:** no path/query parameters; exactly one required `Idempotency-Key` satisfying the phase header policy.
- **Body:** required non-null UUID `connection_id`; `to` has 1–50 unique `EmailStr` values, `cc/bcc` each default to an empty list and contain 0–50 unique `EmailStr` values, with no address repeated across lists; `subject` is non-null 0–300 characters with CR/LF forbidden; plain-text `body` is 1–50,000 characters; optional non-null opaque `reply_to_message_id` and `thread_id` are each 1–1,024 characters and must appear together; no attachments. Reject null lists, unknown fields, raw MIME/provider headers, and every control/header-injection form.
- **Response:** `202 ExternalActionPublic`.
- **Headers:** `Location: /api/v1/external-actions/{id}`, request ID.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 INTEGRATION_SCOPE_MISSING`; `404 INTEGRATION_NOT_FOUND`; `409 IDEMPOTENCY_KEY_REUSED`; `409 INTEGRATION_REAUTH_REQUIRED`; `422 IDEMPOTENCY_KEY_INVALID`; `422 REQUEST_VALIDATION_FAILED` for recipient/header/content/reply/body failures; `429 EXTERNAL_ACTION_QUOTA_EXCEEDED`.
- **Tables:** external action/audit and connection.
- **Transaction:** persist canonical fields, not a caller-supplied MIME blob; worker builds MIME deterministically.
- **Side effects:** worker creates one Google draft; uncertainty is reconciled or marked unknown, not blindly duplicated.
- **Tests:** EmailStr/header injection; correct Reply-To/In-Reply-To/References policy; base64url MIME contract; fake draft ID; duplicate key; model proposal cannot bypass Phase 5 approval.

### Endpoint 11 — Queue explicit draft send

- **Purpose:** send one app-created, already-reviewed provider draft whose content still matches the review.
- **Method/path:** `POST /api/v1/email/drafts/{provider_draft_id}/send`.
- **Auth:** owner, enabled internal `gmail_send` feature, a Google token whose granted scope permits send, and recent auth/explicit human action; model path requires Phase 5 decision.
- **Params/headers:** required non-null UUID query `connection_id`; opaque app-known `provider_draft_id` path 1–1,024 under the phase path policy; exactly one required `Idempotency-Key` satisfying the phase header policy; no other query parameters and no ETag header in this baseline.
- **Body:** exactly `{"confirmation":"send_existing_draft","expected_content_hash":"<64 lowercase hex>"}` with non-null values; reject unknown fields and never accept new content here. The hash came from the succeeded draft-creation action.
- **Response:** `202 ExternalActionPublic`.
- **Headers:** `Location: /api/v1/external-actions/{id}`, request ID.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 FEATURE_FORBIDDEN` when the internal send feature is disabled; `403 INTEGRATION_SCOPE_MISSING`; `403 RECENT_AUTH_REQUIRED`; `404 INTEGRATION_NOT_FOUND`; `404 APP_DRAFT_NOT_FOUND`; `409 IDEMPOTENCY_KEY_REUSED`; `409 INTEGRATION_REAUTH_REQUIRED`; `409 DRAFT_CONTENT_HASH_MISMATCH`; `409 DRAFT_ALREADY_SENT`; `422 IDEMPOTENCY_KEY_INVALID`; `422 DRAFT_CONFIRMATION_INVALID`; `422 REQUEST_VALIDATION_FAILED` for malformed path/query/body; `429 EXTERNAL_ACTION_QUOTA_EXCEEDED`.
- **Tables:** read the connection and succeeded app draft action; insert durable send action/audit.
- **Transaction:** require draft ID and expected content hash to match the app record, then create one send action per logical key; no provider call inline.
- **Side effects:** worker first fetches the current provider draft and compares its normalized content hash, then calls Gmail `drafts.send`. A mismatch fails safely. If Google may have accepted but the response was lost, mark `outcome_unknown` and reconcile; never blind-send again.
- **Tests:** internal send feature absent even though compose scope can technically send; same key one action; unknown/different/hash-changed draft rejected; fake acceptance; lost-response uncertain; approval binds draft ID and hash.

### Endpoint 12 — Retrieve an external action

- **Purpose:** poll Calendar/Gmail write outcome.
- **Method/path:** `GET /api/v1/external-actions/{action_id}`.
- **Auth:** owner.
- **Params/body:** UUID path; no query parameters or body.
- **Response:** `200 ExternalActionPublic`; here `attempts` is the ordered array rather than null. Provider resource ID/ETag is non-null only on known success.
- **Headers:** request ID, `no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID; owner-scoped `404 EXTERNAL_ACTION_NOT_FOUND`.
- **Tables:** action/attempt/audit read.
- **Transaction/side effects:** read only; none.
- **Tests:** every status; other user hidden; secrets/raw provider errors absent; uncertain result has no false success claim.

## 7. Limited reference snippets

Generate state and PKCE with standard-library cryptography primitives:

```python
import base64
import hashlib
import secrets


def create_oauth_secrets() -> tuple[str, str, str, str]:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return state, nonce, verifier, challenge


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("ascii")).hexdigest()
```

Pydantic bounds feature input; the service maps features to scopes:

```python
FEATURE_SCOPES: dict[str, frozenset[str]] = {
    "calendar_read": frozenset({"https://www.googleapis.com/auth/calendar.readonly"}),
    "calendar_write": frozenset({"https://www.googleapis.com/auth/calendar.events"}),
    "gmail_read": frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
    "gmail_compose": frozenset({"https://www.googleapis.com/auth/gmail.compose"}),
    "gmail_send": frozenset({"https://www.googleapis.com/auth/gmail.send"}),
}
BASE_IDENTITY_SCOPES = frozenset({"openid", "email"})


def scopes_for(features: set[str]) -> set[str]:
    requested = {scope for feature in features for scope in FEATURE_SCOPES[feature]}
    return requested | BASE_IDENTITY_SCOPES
```

Validate these exact choices against Google's current scope documentation before release. Do not allow `FEATURE_SCOPES.get(client_string, {client_string})`. Google currently documents `gmail.compose` as able to manage drafts **and send**. Therefore `gmail_compose` versus `gmail_send` must also be an internal enabled-feature/policy distinction; OAuth scope alone cannot express your narrower trust boundary.

Ports keep services fakeable:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CalendarEvent:
    provider_id: str
    etag: str
    summary: str
    start: datetime
    end: datetime
    time_zone: str


class CalendarPort(Protocol):
    def list_events(self, *, access_token: str, query: object) -> tuple[list[CalendarEvent], str | None]: ...
    def create_event(self, *, access_token: str, command: object, provider_event_id: str) -> CalendarEvent: ...
    def update_event(self, *, access_token: str, command: object, expected_etag: str) -> CalendarEvent: ...
```

The adapter owns Google URLs, JSON shapes, timeouts, pagination, error parsing, and token headers. It returns domain types or classified exceptions; it never commits.

Build Gmail MIME from validated fields:

```python
import base64
from email.message import EmailMessage


def encode_draft(*, sender: str, recipients: list[str], subject: str, body: str) -> str:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
```

Do not copy this into the router. `mime.py` adds reply headers and length policies and has fixture-based tests. Pydantic validation plus explicit CR/LF rejection prevents header injection.

## 8. Vertical build slices

1. **Fake OAuth slice:** create/consume transaction, denial/replay handling, fake token response, encrypted connection, list metadata.
2. **Manual live OAuth slice:** dedicated test account, Calendar read scope only. Inspect actual partial scope/token behavior without adding live tests to the default suite.
3. **Calendar read slice:** fake adapter contract, then real adapter; normalize timed and all-day events.
4. **Calendar write slice:** action queue/worker, deterministic event ID, ETag update, retry/uncertain outcomes.
5. **Gmail read slice:** minimal scope, list then MIME-normalized get; persist nothing.
6. **Draft slice:** MIME fixtures and fake adapter, then opt-in live draft.
7. **Send slice:** exact explicit action plus Phase 5 approval for a model proposal; test uncertainty before one manual live send to yourself.
8. **Disconnect slice:** revoke best effort, delete local ciphertext, block work, and verify reauthorization behavior.

## 9. Test strategy

### Unit

- state/verifier/challenge format and injectable entropy/clock;
- feature-to-scope allowlist, partial-capability calculation;
- AES-GCM envelope round trip, randomized nonce, tampered nonce/ciphertext/tag, wrong AAD row/field/provider, unknown key, envelope/size bounds, active-key rotation, and plaintext absent;
- refresh-token preservation truth table;
- connection status/error mapping;
- calendar time/all-day normalization and patch semantics;
- MIME new/reply fixtures, encoded output, header injection and size limits;
- provider error classification: 400 permanent, 401 invalid grant/reauth, 403 missing scope, 404 missing resource, 412 ETag conflict, 429 Retry-After, 5xx transient.

### PostgreSQL integration

- one-time state under concurrent callbacks;
- connection upsert and refresh token preserved on omitted value;
- encrypted bytes only; unique provider account and idempotency keys;
- action claiming/lease recovery using Phase 5 tests;
- owner scope and disconnect/action race;
- migration round trip.

### Fake-provider contract

Create `FakeGoogleOAuth`, `FakeCalendarAdapter`, and `FakeEmailAdapter` implementing the same ports. Make scenarios data-driven: success, timeout-before-send, accepted-then-lost response, 429 with Retry-After, 401 revoked, partial page, malformed provider payload, stale ETag. Assert the service does not know fake-specific details.

Run a shared adapter contract suite against fakes always and real adapters with a mock HTTP transport. Assert method, URL, percent encoding, Authorization header, timeout, request body, cursor, ETag header, response normalization, and secret-safe exception.

### Live tests

Mark `@pytest.mark.live_google`, exclude by default, require an explicit environment switch, dedicated account/calendar, and cleanup confirmation. Never run send in CI. A live smoke test is evidence of configuration, not a substitute for deterministic tests.

## 10. Manual use and debugging

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/integrations/google/authorization-requests \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"features":["calendar_read"],"return_route":"integration_settings"}'

curl -i "http://127.0.0.1:8000/api/v1/calendar/events?connection_id=$AIW_CONNECTION_ID&calendar_id=primary&time_min=2026-08-12T00%3A00%3A00%2B05%3A30&time_max=2026-08-19T00%3A00%3A00%2B05%3A30" \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN"
```

Open the returned authorization URL in a normal browser; do not paste codes/tokens into terminal history. For write requests, use a unique tutorial idempotency key and poll the returned action.

Debug OAuth in sequence: compare registered and sent redirect URI byte-for-byte; locate state hash and expiry; confirm it was consumed once; confirm verifier/challenge pair; inspect only safe HTTP status/error code from token exchange; verify ID-token audience/issuer/nonce; compare requested versus granted scopes; check ciphertext key ID; then test refresh. Never turn on raw HTTP logging around token endpoints.

Debug provider calls using request ID, connection ID, action ID, attempt, operation, safe provider request ID, HTTP status, elapsed time, and retry delay. Do not log query bodies from Gmail, message content, attendees, or bearer tokens.

## 11. Security and reliability checklist

- Exact HTTPS redirect URIs outside localhost; full browser, not embedded webview.
- Random one-use state and PKCE S256; callback replay rejected before token exchange.
- Client-requested raw scopes and return URLs rejected.
- Incremental least privilege; actual grants persisted and checked per operation.
- Gmail read/compose scopes are classified as restricted and may require Google verification and a security assessment when server-side data is stored or transmitted; read the current policy before leaving test-user mode.
- Client secret and token-encryption keys outside source/database; ciphertext includes key ID and supports rotation.
- Access/refresh tokens never returned, logged, or placed in URLs; authorization codes never persisted.
- Existing refresh token preserved if a later response omits one; revocation becomes stable reauthorization state.
- Reads have connect/read/total timeouts, bounded pagination, and normalized errors.
- Writes have durable identity, attempts, provider-specific dedupe/reconciliation, and honest `outcome_unknown`.
- Calendar update uses ETag; create uses provider-compatible deterministic ID where documented.
- Email body/attendee retention minimized; HTML treated as untrusted; attachment fetching remains out of scope.
- Direct human writes are explicit endpoint actions. Model-proposed writes use Phase 5 schema, approval binding, and audit; model text never acts as a bearer credential.
- Disconnect makes credentials unusable locally even if provider revocation is down.

## 12. Exercises and checkpoint

Exercises:

1. Draw the OAuth flow and label which values are public, confidential, one-time, short-lived, or long-lived.
2. Replay a state and prove the fake token endpoint was called exactly once.
3. Return only `calendar.readonly` after requesting Calendar plus Gmail. Show Calendar works and Gmail returns a stable missing-scope error.
4. Reauthorize a connection with no `refresh_token` field. Compare ciphertext before/after and prove it was preserved.
5. Rotate the encryption key: decrypt old-key rows, rewrite with new key ID, retain old decrypt capability until completion, and audit counts without exposing plaintext.
6. Simulate `invalid_grant`; mark reauthorization required and prove workers stop retrying.
7. Create an event across a DST transition using an IANA zone. Explain the stored instant and displayed local time.
8. Fake two concurrent event edits: stale ETag must fail rather than overwrite.
9. Lose the response after Calendar accepted a deterministic ID; retry and recover the same event.
10. Lose the response after Gmail send; prove status becomes outcome unknown and no automatic second send occurs.
11. Construct a reply draft fixture and inspect raw MIME headers. Attempt CR/LF injection and reject it.
12. Disconnect while an action is queued and prove the worker cannot decrypt/use credentials afterward.

Checkpoint:

- OAuth success, denial, missing code, expired/replayed state, partial scopes, refresh preservation, invalid grant, and disconnect are tested.
- Database contains encrypted credentials only and migrations rebuild it.
- Calendar list/create/update and Gmail list/get/draft/send work with fakes by default.
- All provider writes are durable, idempotency-aware, observable, and never run inside HTTP transactions.
- ETag conflicts and uncertain sends are represented honestly.
- A model can propose a draft/send but cannot self-authorize it.
- One manual test account can complete the flow; paid/live/network tests remain opt-in.

## What you should be able to explain after Phase 6

Explain: OAuth roles; own authentication versus delegated authorization; authorization code; state; PKCE; exact redirect URI; offline access; access versus refresh token; incremental and partial scopes; validated provider subject; encryption at rest and key rotation; refresh-token preservation; optimistic credential refresh; port/adapter; normalized provider errors; ETag preconditions; provider versus local idempotency; MIME/base64url; draft versus send; transaction boundaries around network calls; and outcome unknown.

## Official primary documentation

- [Google OAuth 2.0 for web server applications](https://developers.google.com/identity/protocols/oauth2/web-server) — authorization URL, state, offline access, exchange, and refresh.
- [Google OAuth security and storage best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices) and [OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies) — token storage, incremental/partial scopes, redirect security, invalidation.
- [RFC 6749 OAuth 2.0](https://www.rfc-editor.org/rfc/rfc6749) and [RFC 7636 PKCE](https://www.rfc-editor.org/rfc/rfc7636) — protocol semantics behind the provider guide.
- [Google OAuth scopes for APIs](https://developers.google.com/identity/protocols/oauth2/scopes) and [Gmail-specific scope classifications](https://developers.google.com/workspace/gmail/api/auth/scopes) — verify minimum permissions and current sensitive/restricted requirements before implementing a feature.
- [Calendar events list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [create events guide](https://developers.google.com/workspace/calendar/api/guides/create-events), and [events patch](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch) — pagination, IDs, dates, attendee updates, and partial update.
- [Calendar error handling](https://developers.google.com/workspace/calendar/api/guides/errors) — provider retry and conflict classes.
- [Gmail messages list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) and [messages get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get) — sparse lists, formats, queries, pagination.
- [Create and send Gmail drafts](https://developers.google.com/workspace/gmail/api/guides/drafts), [draft create](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create), and [draft send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send) — MIME, base64url, and draft lifecycle.
- [Python `EmailMessage`](https://docs.python.org/3/library/email.message.html#email.message.EmailMessage) — structured MIME construction rather than manual header strings.
- [`cryptography` AESGCM authenticated-encryption recipe](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM) — supported key/nonce sizes, authentication tags, AAD, and the nonce-reuse warning behind the exact envelope above.
- [HTTP conditional requests](https://www.rfc-editor.org/rfc/rfc9110.html#name-conditional-requests) — why an ETag precondition prevents blind overwrite.

Do not add Gmail send scope until Calendar writes and Gmail drafts have reliable action records. The riskiest operation should be the last one you enable.
