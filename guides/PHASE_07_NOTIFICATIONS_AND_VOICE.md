# Phase 7 — Verified Notifications, Webhooks, Inbound Commands, and Voice

This phase turns due reminders into durable Telegram, email, and Slack deliveries, accepts authenticated provider events without processing them twice, and transforms audio into a transcript and **proposed** tool actions. A transcript can be wrong; it is input, never authorization.

## Outcome, prerequisites, and non-goals

By the end you can:

- pair and verify channel destinations before ordinary notifications are allowed;
- queue/poll Telegram, email, and Slack deliveries with leases, attempts, `429` handling, honest uncertain outcomes, and dead-letter visibility;
- authenticate, deduplicate, store, and quickly acknowledge Telegram and Slack webhooks;
- normalize inbound commands into Phase 5 tool proposals instead of direct side effects;
- upload bounded audio, verify its real type/duration, retain/delete it by policy, transcribe through a port, and expose exact Phase 5 decisions for proposed writes.

Prerequisites: verified local users, reminder scheduling from Phase 3, Phase 5 durable work and approvals, PostgreSQL/Alembic, HTTPS for public webhooks, and provider test/sandbox accounts. Follow the curriculum order and complete Phase 6 first: this chapter deliberately reuses its HTTPX runtime dependency, encrypted credential keyring, durable revocation contract, and provider-adapter conventions. Notification business logic remains independent of Google-specific adapters.

Non-goals: mass messaging, arbitrary recipient URLs, storing audio in PostgreSQL, voice identity/biometrics, wake words, phone calls, automatic execution of destructive speech, marketing email, guaranteed human receipt, and exactly-once provider delivery.

## 1. Beginner concepts

A **channel** is a verified destination belonging to one user: a Telegram chat, email address, or Slack channel/webhook. “The user typed this address” is not verification. Pair it with an expiring one-time challenge or a provider OAuth installation.

A **notification delivery** is durable intent to send one event to one channel. It is distinct from a reminder schedule. A reminder may be triggered once while three channel deliveries have three different outcomes.

Provider success usually means **accepted by the provider**, not displayed or read by a human. Name the state `accepted`; use `delivered` only when an authenticated provider receipt actually proves that stronger fact.

A **webhook** reverses the usual direction: a provider calls your API. HTTPS does not by itself prove which provider sent it. Verify a Telegram webhook secret or Slack HMAC signature before trusting the body. Provider retries make duplicate events normal, so persist a unique provider event identity.

**At-least-once** means a delivery worker or webhook provider may attempt the same logical work more than once. Local idempotency, unique constraints, provider IDs, and reconciliation reduce duplicates; they do not magically make a non-idempotent remote API exactly once.

Audio has three different claims:

1. bytes are a supported audio container/codec;
2. transcription says words were probably spoken;
3. the authenticated workspace user authorizes an action.

Only the normal auth/approval system can establish claim 3.

## 2. Architecture and state machines

```text
due-reminder claimer -> one DB transaction:
                         lock due schedule
                         + select all verified/enabled channels
                         + create one deterministic delivery per channel
                         + mark triggered only when at least one delivery exists
                                  |
delivery workers -> claim/lease -> provider adapters -> attempts/final state

Telegram/Slack -> webhook router -> verify raw request -> dedupe/store -> 2xx
                                                |
                                       command worker -> proposed tool runs
                                                             |
                                                     Phase 5 policy/approval

multipart upload -> controlled storage + voice row -> transcription worker
                                  -> intent proposer -> Phase 5 tool runs
```

Channel state:

```text
pending_verification -> verified -> disabled -> verified
pending_verification --------------------------> deleted
verified/disabled -----------------------------> deleted
```

Changing a destination or credential is not a patch to a verified target. Create/re-pair a new channel so old proof cannot authorize a new address.

Delivery state:

```text
queued -> sending -> accepted
                  -> retry_scheduled -> sending
                  -> failed_permanent
                  -> dead_letter
                  -> outcome_unknown
queued -> cancelled_before_start
sending -> cancel_requested -> accepted|failed_permanent|outcome_unknown
```

`accepted`, `failed_permanent`, `dead_letter`, `outcome_unknown`, and `cancelled_before_start` are terminal. An automatic retry is legal only for a classified transient failure where the adapter knows the provider did not accept, or where provider idempotency/reconciliation is defined.

Voice processing state:

```text
uploaded -> queued_transcription -> transcribing -> transcribed
  -> interpreting -> proposals_ready -> completed
                     \-> no_action
any processing state -> failed
```

Audio retention has a separate `retained|delete_queued|deleted` state. Do not overload the processing state.

## 3. Dependencies, local tools, configuration, and folders

Add runtime dependencies:

```toml
"python-multipart>=0.0.12,<1",
"filetype>=1.2,<2",
"mutagen>=1.47,<2",
```

HTTPX and `cryptography` already exist from Phase 6. `python-multipart` lets FastAPI stream multipart uploads. `filetype` checks byte signatures instead of trusting filenames/MIME declarations. `mutagen` reads duration/metadata for supported containers. Keep the allowlist small and test real fixtures; a production media pipeline may replace these with an isolated scanner/transcoder.

Configuration names in `.env.example`:

```dotenv
APP_TELEGRAM_BOT_TOKEN=CHANGE_ME
APP_TELEGRAM_WEBHOOK_SECRET=CHANGE_ME
APP_TELEGRAM_WEBHOOK_PATH_TOKEN=CHANGE_ME_OPAQUE_PATH
APP_SLACK_CLIENT_ID=CHANGE_ME
APP_SLACK_CLIENT_SECRET=CHANGE_ME
APP_SLACK_REDIRECT_URI=http://127.0.0.1:8000/api/v1/oauth/callbacks/slack
APP_SLACK_FRONTEND_RESULT_URL=http://127.0.0.1:3000/settings/notifications/slack
APP_SLACK_SIGNING_SECRET=CHANGE_ME
APP_SLACK_OAUTH_BINDING_COOKIE=aiw_slack_oauth_binding
APP_CHANNEL_CHALLENGE_PEPPER=CHANGE_ME
APP_EMAIL_SMTP_HOST=CHANGE_ME
APP_EMAIL_SMTP_PORT=587
APP_EMAIL_SMTP_USERNAME=CHANGE_ME
APP_EMAIL_SMTP_PASSWORD=CHANGE_ME
APP_EMAIL_FROM_ADDRESS=CHANGE_ME
APP_EMAIL_SMTP_STARTTLS=true
APP_VOICE_STORAGE_ROOT=./var/voice
APP_VOICE_MAX_BYTES=15728640
APP_VOICE_MAX_SECONDS=120
APP_VOICE_DEFAULT_RETENTION_HOURS=24
```

Email SMTP/API credentials, Slack client secret/tokens/webhook URLs, and Telegram tokens are secrets. Put application-wide credentials in the environment/secret manager; encrypt per-installation/channel secrets using the **exact Phase 6 AES-256-GCM envelope**, fresh 96-bit nonce, active-key/rotation protocol, and canonical AAD builder. Use server-owned AAD contexts from that allowlist: Slack installation token/webhook URL and revocation credential use provider `slack`; channel destination and encrypted email challenge code use provider `internal`. The row UUID and exact field name must match on decrypt, so copied ciphertext fails authentication. Run Phase 6 tamper/wrong-AAD/unknown-key/rotation tests against these new row types too. Slack's redirect/result URLs are fixed startup configuration. Require certificate-validated email TLS outside a named local-fake profile. Keep the voice directory private, unserved, and ignored by Git.

Folder additions:

```text
app/
├── modules/notifications/
│   ├── model.py
│   ├── schemas.py
│   ├── repository.py
│   ├── service.py
│   ├── verification.py
│   ├── ports.py
│   ├── worker.py
│   ├── router.py
│   └── dependencies.py
├── modules/webhooks/
│   ├── model.py
│   ├── repository.py
│   ├── telegram_router.py
│   ├── slack_router.py
│   ├── slack_oauth.py
│   ├── verification.py
│   └── command_worker.py
├── modules/voice/
│   ├── model.py
│   ├── schemas.py
│   ├── storage.py
│   ├── media_validation.py
│   ├── ports.py
│   ├── service.py
│   ├── worker.py
│   └── router.py
└── providers/
    ├── telegram/adapter.py
    ├── slack/adapter.py
    ├── email/adapter.py
    └── transcription/adapter.py
tests/
├── fixtures/audio/
├── unit/notifications/
├── unit/webhooks/
├── unit/voice/
├── integration/notifications/
├── integration/webhooks/
├── integration/voice/
└── contract/providers/
```

Use deterministic fake adapters by default. Add exactly one real transcription adapter only after the port contract passes; follow that provider's current official upload, retention, model, rate-limit, and privacy documentation. Live Telegram/Slack/email/transcription tests are explicit and excluded from CI.

## 4. Tables, constraints, indexes, and migration

### `notification_channels`

- `id uuid`, `user_id` FK, `kind` constrained to `telegram|email|slack`.
- `status` constrained to `pending_verification|verified|disabled`.
- safe label/display and keyed-HMAC destination fingerprint; encrypted destination/config ciphertext plus key ID. Do not use a bare email hash that permits dictionary guessing.
- provider-safe IDs after pairing: Telegram chat ID or Slack installation/team/channel ID where applicable. Do not expose webhook URL or bot token.
- `verified_at`, `disabled_at`, nullable `deleted_at`, timestamps. A deleted row is a safe tombstone with ciphertext/provider target erased.

Index owner/kind/status. Destination uniqueness is provider-specific; at minimum use a partial unique index on `(user_id, kind, destination_fingerprint)` where `deleted_at IS NULL`. Do not enforce global email uniqueness because multiple users may legitimately share a destination.

### `slack_oauth_transactions` and `slack_installations`

Slack transaction columns mirror the Google safety pattern: user, unique state hash, initiating-browser-binding hash, exact scope set/redirect key, expiry, consumed time, and safe outcome. Store no authorization code. Allow one unconsumed Slack flow per local user.

An installation stores local user, unique Slack team ID, installer Slack user ID, app/bot IDs, actual scopes, status, and encrypted bot access/refresh tokens if rotation is enabled. The OAuth response's `incoming_webhook` supplies an encrypted URL plus channel ID/name; the callback creates or rotates one **verified** Slack notification channel linked to this installation. Unique active `(user_id, team_id, channel_id)` prevents duplicates. Ordinary inbound `app_mention` commands are accepted only from the recorded installer user in the selected channel for this personal baseline.

### `channel_verification_challenges`

- `id`, `channel_id` FK, purpose, `secret_digest`, nullable encrypted secret/key ID, `expires_at`, attempt/max-attempt counts, `consumed_at`, timestamps.
- Only one active challenge per channel, enforced using a transaction/advisory pattern or partial unique index on unconsumed rows.

Six-digit email codes have low entropy. Store `HMAC-SHA256(server_pepper, channel_id || challenge_id || code)`, not a bare hash. The email worker temporarily needs the code, so also encrypt it and wipe that ciphertext after accepted send, expiry, or consumption. A Telegram nonce is longer and URL-safe; return it once and store only its digest. Slack channels are verified by OAuth and do not use this table. Rate-limit creation and attempts.

### `notification_deliveries` and `notification_delivery_attempts`

Delivery columns: user/channel, nullable `reminder_id` FK **with `ON DELETE SET NULL`**, purpose `notification|verification`, event type, server-normalized payload JSONB, payload hash, idempotency key, provider message ID, status, accepted/delivered times, and the complete Phase 5 attempt/lease/retry/cancel/error fields. A Phase 3 hard delete removes the reminder resource, not historical evidence that a delivery was attempted; the database therefore detaches the reference and retains delivery/attempt/audit rows.

Use unique `(user_id, idempotency_key)` and a partial unique index on `(reminder_id, channel_id, event_type)` where `reminder_id IS NOT NULL` so two reminder schedulers converge. `reminder_id` is nullable because manual notifications and verification challenges are not reminders. A check allows a pending channel only when purpose is `verification` and the payload came from a server-owned template. Attempts store response class, safe provider request/message ID, elapsed time, and outcome—never channel secrets or full message text.

### Exact Phase 3 reminder-to-delivery refactor

The Phase 3 command must stop marking a due reminder triggered by itself. In this baseline, a reminder has no per-reminder channel preference: its target set is **all** of that reminder owner's channels that are `verified`, enabled, and not deleted at materialization time. A later preference feature must use an explicit owner-checked join table and must fall back to this documented default only when the preference mode is `all_enabled`; never infer a preference from the last channel used.

Add internal `reminders.next_trigger_attempt_at TIMESTAMPTZ NOT NULL`. Backfill every existing row from `remind_at`. Every successful mutation of `remind_at`—Phase 3 create, PATCH reschedule, or snooze—must set `next_trigger_attempt_at=remind_at` in the **same transaction**, whether the new instant moves earlier or later. Replace the old due index with `(status, next_trigger_attempt_at, id)`. This field need not enter the Phase 3 public response. It prevents a user with no verified channel from creating a hot loop.

`ReminderDeliveryService.materialize_due(limit, now)` performs this algorithm for each claimed reminder in **one PostgreSQL transaction**:

1. Select a `scheduled` row with `next_trigger_attempt_at <= now`, ordered by that field and ID, using `FOR UPDATE SKIP LOCKED`. Recheck `remind_at <= now` under the lock. If a legacy bug/race left `remind_at > now`, repair `next_trigger_attempt_at=remind_at`, commit, and skip it; never repeatedly reclaim a future schedule.
2. Select every owner channel whose status is `verified`, enabled, and not deleted, ordered by channel ID, with `FOR KEY SHARE`. Channel enable/disable/delete updates take a conflicting row lock, so selection has a serialized meaning.
3. If the target set is empty, do **not** set `status=triggered` and do not insert a delivery. Keep it `scheduled`, set `next_trigger_attempt_at = now + interval '15 minutes'`, append safe audit code `NO_VERIFIED_NOTIFICATION_CHANNEL`, and commit. Verification of a new channel may also move this field down to `LEAST(current, now)` so a newly usable channel need not wait fifteen minutes.
4. For each selected channel, construct the normalized server-owned payload snapshot from the locked reminder. Set `event_type="reminder_due"`, `purpose="notification"`, idempotency key `reminder-due:v1:{reminder_id}:{channel_id}`, and delivery UUID `uuid5(REMINDER_DELIVERY_NAMESPACE, idempotency_key)`. Insert with status `queued`. The unique user/key constraint and the partial reminder/channel/event index are the final race defense.
5. Assert that the number of newly inserted or already-existing matching rows is greater than zero and exactly equals the selected target count. Only then update the reminder to `status=triggered`, `triggered_at=now`; its existing Phase 3 check still requires that timestamp. Commit delivery rows, audit events, and schedule transition together.

There is no provider call in this transaction. A crash before commit leaves the reminder scheduled and creates zero visible rows; a crash after commit leaves it triggered with the complete deterministic set. Do not catch an insertion error, commit a trigger anyway, or mark a zero-target reminder triggered. Delivery failures later do not roll the schedule back: they are independent, visible outcomes.

Build the migration in this order: create delivery tables/checks with `ForeignKey("reminders.id", ondelete="SET NULL")`; add and backfill `next_trigger_attempt_at`; make it non-null and replace the due index; update create/PATCH/snooze scheduling services; add the partial unique reminder-delivery index; then replace the Phase 3 command implementation. Do not rely on ORM cascade/default behavior. Migration tests must prove backfill and upgrade/downgrade/upgrade. PostgreSQL tests must cover zero channels, one/three channels, a disabled channel, deterministic IDs, a repeated invocation, two concurrent claimers, channel-disable serialization, PATCH to an earlier and later instant updating both timestamps atomically, a deliberately stale due-key repaired to its future `remind_at` without a hot loop, concurrent PATCH versus materialization (row locking yields either the complete old occurrence or the fully rescheduled new occurrence, never mixed fields/partial deliveries), an injected insert failure (zero committed rows and still scheduled), a successful transaction (all rows plus triggered state), and hard deletion of a triggered reminder (delivery survives, `reminder_id` becomes null, attempts/audit survive).

### `webhook_events` and `inbound_commands`

`webhook_events`: provider, provider event ID, raw-body SHA-256, verified timestamp, processing state, bounded safe metadata/payload subset, received/processed timestamps; unique `(provider, provider_event_id)`. Invalid requests are rejected before insertion; track aggregate metrics rather than storing attacker payloads.

`inbound_commands`: webhook event FK unique, user/channel FKs after mapping, normalized text with a short retention limit or encrypted form, status, parse error, timestamps. A join table `inbound_command_tool_runs` links ordered Phase 5 run IDs.

### `voice_commands`

- owner, processing status, audio state, random storage key, declared/detected media type, byte size, duration, SHA-256.
- optional language hint/detected language, transcript with retention classification, safe error.
- `audio_delete_after`, `audio_delete_requested_at`, `audio_deleted_at`, processing/lease fields.
- `voice_command_tool_runs` links proposed Phase 5 runs; each stores the exact proposal order and no separate authorization flag.

Never store audio bytes in PostgreSQL or use the uploaded filename as storage key. Index due work and retention deletion. Add checks for positive bounded size/duration, deletion-state consistency, and timestamps required by terminal states.

Create one Alembic revision or a small ordered series. Inspect `ON DELETE` choices carefully: deleting a channel must not erase audit/delivery evidence; prefer soft status plus encrypted-secret deletion, then retain safe history. Migration tests run upgrade/downgrade/upgrade on a disposable database.

## 5. Request-schema policy

All Pydantic JSON models set `model_config = ConfigDict(extra="forbid")`; every unknown field is `422`. Strings have explicit length bounds and are stripped only where whitespace is semantically irrelevant. Omitted and `null` are different: no endpoint below accepts `null` unless explicitly stated. Multipart accepts only the named parts and rejects unexpected form fields.

Every authenticated JSON body is at most 65,536 bytes and includes at most 1,000 total values, depth 8, 100 keys per object, 100 array entries, and 8,192 UTF-8 bytes per string unless an endpoint sets a smaller limit. An authenticated request's raw query component is at most 4,096 bytes and 20 pairs. Every authenticated response includes `X-Request-ID` and `Cache-Control: no-store`. Errors use the common envelope. Provider webhooks have provider-specific response bodies but still attach a request ID.

The channel-list cursor is an unpadded base64url ASCII string of 1–2,048 characters. It decodes to at most 512 bytes of UTF-8 JSON with exactly version `1`, the canonical filter fingerprint, and the last `(created_at,id)` key. Ordering is always `created_at DESC, id DESC`; cursor continuation uses the corresponding strict tuple comparison, and `limit` is not part of the fingerprint. An omitted cursor starts the first page; `null` is rejected. Non-string, empty, overlong, padded, or non-base64url input is `422 REQUEST_VALIDATION_FAILED`. Invalid UTF-8/JSON, missing/extra fields, unsupported version, malformed timestamp/UUID, trailing data, or filter mismatch is `400 INVALID_CURSOR`.

`Idempotency-Key` follows Phase 6 exactly: one required 8–200-character value containing only visible non-space ASCII `0x21..0x7E`, retained byte-for-byte. Missing, duplicate, shorter, longer, non-ASCII, space, or control input is `422 IDEMPOTENCY_KEY_INVALID`. A surfaced `Retry-After` is one integer clamped to 1–3,600 seconds; malformed/missing provider values use configured backoff.

Both provider webhook endpoints reject a raw body over **262,144 bytes** before JSON parsing: reject an excessive `Content-Length`, but also count streamed bytes because the header can be missing or false. After signature/secret verification, the dedicated provider DTO rejects JSON deeper than 16, over 5,000 total values, over 256 keys in one object, over 500 array items, or any string over 16,384 UTF-8 bytes. These are transport limits, not permission to persist the whole provider object.

Telegram's configured opaque path token is 43–128 URL-safe characters and its secret header is 1–256 documented-safe characters. Extract only: `update_id` integer `0..2^63-1`; chat and user IDs as validated signed/unsigned decimal strings of 1–20 characters; message timestamp integer `0..2^63-1`; and one text/caption of 1–4,096 Unicode characters. `/connect` accepts exactly one 43–128 character URL-safe nonce; the retained normalized command is 1–4,096 characters. Oversize extracted fields return `400 WEBHOOK_INVALID`; never truncate before hashing/deduplication.

Slack requires a decimal `X-Slack-Request-Timestamp` of 1–20 ASCII digits and a signature of exactly `v0=` plus 64 lowercase hexadecimal characters. Extract outer `type` of 1–80 ASCII characters; `event_id` 1–128; team/channel/user IDs 1–64 `[A-Z0-9]`; event text 1–4,000 Unicode characters; and URL-verification challenge 1–256 characters. Oversize text/challenge is `400 WEBHOOK_INVALID`, not truncation or reflection. Only the exact bounded challenge may be echoed after a valid signature.

The Slack OAuth callback separately rejects a query component over 4,096 bytes before parsing. It accepts at most 12 pairs and one value per key: `state` 43–256 URL-safe; `code` 1–1,024; `error` 1–100 ASCII `[A-Za-z0-9_.-]`; `error_description` 0–500; `error_uri` 0–1,024; and `scope` 0–4,096. Duplicate keys, controls, invalid UTF-8/percent encoding, simultaneous code/error, or any other callback-shape violation return `400 OAUTH_RESPONSE_INVALID`. At most eight unknown keys may be ignored only when their names are 1–64 ASCII `[A-Za-z0-9_.-]` and values at most 256 characters; otherwise return `400 OAUTH_RESPONSE_INVALID`. Never log, store, or reflect code, provider descriptions/URIs, or unknown fields.

### Mandatory secret-safe request-logging verification and hardening gate

The Telegram route deliberately contains an opaque authentication token. Phase 1 already introduced the shared `route_template_for()`/`<unmatched>` pattern; preserve it and verify that **both** request middleware and the catch-all exception handler still use it. Before registering Endpoint 9, ensure its application-owned label is exactly `/api/v1/webhooks/telegram/{opaque_path_token}` and that pre-match failures use a fixed redacted fallback. Never log the raw path, `request.url`, query string, request target, Telegram header, or Uvicorn request line. The equivalent limited helper below shows the contract if you are reviewing or repairing that foundation code:

```python
from starlette.requests import Request


def safe_route_label(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template  # Application-owned template, not attacker input.

    # Before/no route match, inspect only to choose a fixed label; never return it.
    path = request.scope.get("path")
    if isinstance(path, str) and path.startswith("/api/v1/webhooks/telegram/"):
        return "/api/v1/webhooks/telegram/{opaque_path_token}"
    return "<unmatched>"
```

In middleware, call the shared helper only after `await call_next(request)` returns or throws; emit it as structured field `route`, not as message interpolation. The catch-all uses that same helper. A pre-routing failure or `404` gets one of the two fixed fallbacks, never raw input. Also disable Uvicorn's default access logger for the webhook runtime (`uvicorn ... --no-access-log`) and rely on this structured completion log; otherwise its raw request line defeats the application redaction. Configure any reverse proxy similarly. Keep `method`, route template, status, duration, and request ID; exclude all query/body/header values.

Tests seed a unique fake path token and query secret, then capture every application/access log for: valid webhook success, authenticated handler exception, wrong-token response, and unmatched `404`. Assert neither secret nor raw request target occurs anywhere; assert the known route is exactly `/api/v1/webhooks/telegram/{opaque_path_token}` and unmatched traffic is `<unmatched>`. Also test the catch-all directly. Treat this as a migration gate, not optional logging polish.

### Normative response vocabulary

Every timestamp is a UTC RFC 3339 string with `Z`. Every listed key is always present; only a type containing `| null` may be null. Objects have no additional public keys, and no ciphertext, secret, storage key, raw provider body, or traceback appears.

- **`RedactedDestinationPublic`:** `display: string` 1–200 (masked email, Telegram chat label, or Slack team/channel label); `provider_team_id: string 1–64 | null`; `provider_channel_id: string 1–64 | null`. Email has both IDs null; Telegram uses `provider_channel_id` for the validated chat ID and team null.
- **`NotificationChannelPublic`:** `id: UUID`; `kind: "email"|"telegram"|"slack"`; `label: string` 1–80; `status: "pending_verification"|"verified"|"disabled"`; `enabled: boolean` (true exactly for usable verified state); `destination: RedactedDestinationPublic`; `verified_at: datetime | null`; `disabled_at: datetime | null`; `created_at: datetime`; `updated_at: datetime`. Deleted tombstones are not returned by ordinary endpoints.
- **`NotificationSafeErrorPublic`:** `code: string` 1–80 ASCII; `message: string` 1–500; `details: object | null` with at most 20 keys and 8,192 canonical UTF-8 bytes. It contains no destination, message, exception, or provider response.
- **`NotificationDeliveryPublic`:** `id: UUID`; `channel_id: UUID`; `reminder_id: UUID | null`; `purpose: "notification"|"verification"`; `event_type: "manual"|"reminder_due"|"email_verification"`; `status: "queued"|"sending"|"retry_scheduled"|"cancel_requested"|"cancelled_before_start"|"accepted"|"delivered"|"failed_permanent"|"dead_letter"|"outcome_unknown"`; `attempt_count: integer >= 0`; `max_attempts: integer >= 1`; `next_attempt_at: datetime` (always present; status determines claimability); `provider_message_id: string 1–255 | null`; `accepted_at: datetime | null`; `delivered_at: datetime | null`; `safe_error: NotificationSafeErrorPublic | null`; `created_at: datetime`; `started_at: datetime | null`; `finished_at: datetime | null`; `status_url: string` equal to `/api/v1/notification-deliveries/{id}`.
- **`NotificationDeliveryAttemptPublic`:** `attempt_number: integer >= 1`; `outcome: "executing"|"accepted"|"retryable_failure"|"permanent_failure"|"lease_expired"|"outcome_unknown"`; `started_at: datetime`; `finished_at: datetime | null`; `duration_ms: integer >= 0 | null`; `provider_request_id: string 1–255 | null`; `provider_message_id: string 1–255 | null`; `safe_error: NotificationSafeErrorPublic | null`.
- **`NotificationChannelRefPublic`:** `id: UUID`; `kind: "email"|"telegram"|"slack"`; `label: string` 1–80; `destination: RedactedDestinationPublic`.
- **`NotificationDeliveryDetail`:** every `NotificationDeliveryPublic` key plus `channel: NotificationChannelRefPublic`; `attempts: array[NotificationDeliveryAttemptPublic]` ordered by attempt number and capped at `max_attempts`. Message/payload text is intentionally absent.
- **`VoiceCommandPublic`:** `id: UUID`; `status: "uploaded"|"queued_transcription"|"transcribing"|"transcribed"|"interpreting"|"proposals_ready"|"completed"|"no_action"|"failed"`; `audio_state: "retained"|"delete_queued"|"deleted"`; `declared_media_type: string 1–255 | null`; `detected_media_type: "audio/wav"|"audio/mpeg"|"audio/mp4"|"audio/ogg"`; `size_bytes: integer 1..15,728,640`; `duration_ms: integer 1..120,000`; `language_hint: string 2–35 | null`; `transcript_available: boolean`; `audio_delete_after: datetime`; `audio_deleted_at: datetime | null`; `created_at: datetime`; `updated_at: datetime`; `status_url: string` equal to `/api/v1/voice-commands/{id}`.
- **`VoiceProposalPublic`:** `order: integer >= 1`; `tool_run_id: UUID`; `tool_name: string` matching Phase 5; `tool_version: integer >= 1`; `risk: "read"|"write"|"destructive"`; `status: Phase 5 ToolRunSummary.status`; `input_hash: 64 lowercase hex`; `decision_url: string | null` (non-null only while a decision may be submitted).
- **`VoiceCommandDetail`:** every `VoiceCommandPublic` key plus `transcript: string 1–100,000 | null`; `detected_language: string 2–35 | null`; `transcription_provider: "fake"|"google_cloud_speech"|null`; `transcription_model: string 1–100 | null`; `transcription_request_id: string 1–255 | null`; `safe_error: NotificationSafeErrorPublic | null`; `proposals: array[VoiceProposalPublic]` ordered by `order` and capped at 50. Transcript/provenance are null until a successful transcription; proposals are empty until interpretation.

List envelopes are exactly `{ "items": array[T], "next_cursor": string | null }`.

### Normative Phase 7 error vocabulary

Each expected failure below has exactly one HTTP status and stable `error.code`. Do not substitute a provider string or collapse owner-scoped not-found cases into a generic bare status.

| Status and code | Exact condition |
|---|---|
| `400 OAUTH_RESPONSE_INVALID` | Slack callback shape, encoding, field, pair, or total-query contract fails. |
| `400 OAUTH_STATE_INVALID` | Slack state is unknown, malformed after surface validation, already consumed, or does not match. |
| `400 OAUTH_STATE_EXPIRED` | Matching Slack authorization transaction expired. |
| `400 OAUTH_BROWSER_BINDING_INVALID` | Initiating-browser cookie is missing or does not match. |
| `400 INVALID_CURSOR` | Surface-valid cursor fails semantic decode/schema/filter/order binding. |
| `400 WEBHOOK_INVALID` | Authenticated provider body/required extracted field violates its body, JSON, structure, or field contract. |
| `400 MULTIPART_INVALID` | Multipart encoding, boundary, part count/name, or duplicate-part contract fails. |
| `401 INVALID_ACCESS_TOKEN` | Bearer token is missing, invalid, or expired. |
| `401 WEBHOOK_UNAUTHENTICATED` | Telegram path/header or Slack signature authentication fails. |
| `401 WEBHOOK_STALE` | Validly shaped Slack timestamp is outside the five-minute replay window. |
| `403 RECENT_AUTH_REQUIRED` | Authenticated user must perform recent local reauthentication. |
| `403 SLACK_INSTALL_FORBIDDEN` | Server policy disables Slack installation for this user/workspace. |
| `404 CHANNEL_NOT_FOUND` | Owner-scoped channel is absent/deleted/foreign. |
| `404 CHALLENGE_NOT_FOUND` | Owner-scoped challenge is absent, superseded, or belongs to another channel/user. |
| `404 DELIVERY_NOT_FOUND` | Owner-scoped delivery is absent/foreign. |
| `404 VOICE_COMMAND_NOT_FOUND` | Owner-scoped voice command is absent/foreign. |
| `409 CHANNEL_ALREADY_EXISTS` | Active channel uniqueness conflicts with the requested destination/provider identity. |
| `409 CHANNEL_ALREADY_VERIFIED` | Verification operation targets an already verified channel. |
| `409 CHANNEL_NOT_VERIFIED` | Enable/disable or ordinary delivery targets a pending channel. |
| `409 CHANNEL_DISABLED` | Challenge or delivery targets a disabled channel. |
| `409 CHALLENGE_EXPIRED` | Matching verification challenge has expired. |
| `409 CHALLENGE_CONSUMED` | Matching challenge was already used. |
| `409 VERIFICATION_METHOD_MISMATCH` | Email-code endpoint targets a Telegram/Slack challenge or channel. |
| `409 IDEMPOTENCY_KEY_REUSED` | Same user/operation/key has a different canonical payload hash. |
| `413 AUDIO_TOO_LARGE` | Stream exceeds the active byte or duration-adapter preflight ceiling. |
| `415 AUDIO_TYPE_UNSUPPORTED` | Detected signature/codec is unsupported or conflicts with declared type. |
| `422 REQUEST_VALIDATION_FAILED` | Common path/query/body type, null, unknown-field, UUID, or surface-cursor validation fails. |
| `422 SLACK_OAUTH_REQUIRED` | Client tries to create a Slack channel through the generic channel body. |
| `422 CHANNEL_FILTER_INVALID` | `kind` or `status` is not an allowed non-null literal. |
| `422 PAGE_LIMIT_INVALID` | List limit is not an integer in `1..100`. |
| `422 IDEMPOTENCY_KEY_INVALID` | Required header violates the exact header contract. |
| `422 CODE_INVALID` | Verification code is not exactly six ASCII digits. |
| `422 AUDIO_INVALID` | Audio is corrupt, duration cannot be validated, language/retention is invalid, or media violates a non-type semantic rule. |
| `429 CHANNEL_QUOTA_EXCEEDED` | User's channel-count quota is exhausted. |
| `429 AUTHORIZATION_RATE_LIMITED` | Too many Slack authorization starts. |
| `429 CHALLENGE_RATE_LIMITED` | Challenge rotation/send rate is exceeded. |
| `429 TOO_MANY_ATTEMPTS` | Challenge's wrong-code attempt ceiling is reached. |
| `429 USER_DELIVERY_QUOTA_EXCEEDED` | Manual notification queue quota is exhausted. |
| `429 VOICE_QUOTA_EXCEEDED` | Voice upload/request-rate quota is exhausted. |
| `429 VOICE_STORAGE_QUOTA_EXCEEDED` | Retained-audio byte quota is exhausted. |
| `500 INTERNAL_SERVER_ERROR` | Unexpected failure; request ID is safe, internals are not returned. |

## 6. Full endpoint worksheets

### Endpoint 1 — Create a pending notification channel

- **Purpose:** register one destination without treating it as verified.
- **Method/path/auth:** `POST /api/v1/notification-channels`; bearer user required.
- **Path/query params:** none.
- **Body:** discriminated union. Email: `{"kind":"email","label":str(1..80),"destination":{"address":EmailStr}}`; Telegram: `{"kind":"telegram","label":str(1..80)}`. No field is nullable; unknown keys forbidden. `kind=slack` returns `422 SLACK_OAUTH_REQUIRED`; Slack channels are created only by S1/S2.
- **Success:** `201 NotificationChannelPublic` with ID/kind/label/status=`pending_verification`, redacted destination, timestamps.
- **Headers:** `Location: /api/v1/notification-channels/{id}`, request ID, `Cache-Control: no-store`.
- **Errors/status:** `401 INVALID_ACCESS_TOKEN`; `409 CHANNEL_ALREADY_EXISTS`; `422 SLACK_OAUTH_REQUIRED`; `422 REQUEST_VALIDATION_FAILED` for malformed email, Telegram destination, null, unknown, or other schema failure; `429 CHANNEL_QUOTA_EXCEEDED`.
- **Tables:** insert channel; encrypt the email destination before flush; audit.
- **Transaction/side effects:** one commit; no provider call or verification send.
- **Tests:** email/Telegram unions; malformed email; Slack/raw URL rejected; Telegram rejects destination; ciphertext only; unknown/null fields; duplicate/race; safe response.

### Endpoint S1 — Create a Slack installation authorization request

- **Purpose:** bind installation to the authenticated local user and let Slack's OAuth channel picker create a verified incoming webhook.
- **Method/path/auth:** `POST /api/v1/notification-channels/slack/authorization-requests`; bearer user plus recent auth required.
- **Path/query parameters and body:** no path/query parameters; body exactly `{"return_route":"notification_settings"}` with non-null closed literal and unknown fields forbidden.
- **Success:** `201 {"authorization_request_id":UUID,"authorization_url":"https://slack.com/oauth/v2/authorize?...","expires_at":datetime}`.
- **Headers:** request ID/no-store and a random HttpOnly SameSite=Lax browser-binding cookie with `Path=/api/v1/oauth/callbacks/slack`, Secure outside localhost.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 RECENT_AUTH_REQUIRED`; `403 SLACK_INSTALL_FORBIDDEN`; `422 REQUEST_VALIDATION_FAILED`; `429 AUTHORIZATION_RATE_LIMITED`.
- **Tables:** consume an older active Slack transaction and insert state/binding hashes, exact redirect, and server-owned scopes `incoming-webhook,app_mentions:read`.
- **Transaction/side effects:** commit before returning URL; no Slack call. The URL includes configured client ID, exact redirect URI, state, and allowlisted scopes only.
- **Tests:** raw scopes/team/redirect rejected; entropy/expiry/cookie flags; only hashes stored; URL exact; concurrent starts leave one active flow.

### Endpoint S2 — Slack OAuth callback

- **Purpose:** consume Slack approval/denial, exchange the code, and create a verified team/channel mapping.
- **Method/path/auth:** `GET /api/v1/oauth/callbacks/slack`; no bearer; exact state plus initiating-browser cookie required.
- **Path/query parameters and body:** no path/body; the query follows the exact 4,096-byte/pair/field policy above, with `state` and exactly one of `code` or `error`. Only unknown fields within that policy are ignored and never reflected.
- **Success:** `303 See Other` to the fixed frontend result URL containing only local result/transaction ID.
- **Headers:** `Location: {APP_SLACK_FRONTEND_RESULT_URL}?transaction_id=<local-UUID>&result=<connected|limited|denied|failed>` with normal URL encoding and no other query/fragment; `Cache-Control: no-store`; `Referrer-Policy: no-referrer`; request ID; and a binding cookie cleared with `Max-Age=0` plus the exact attributes/path used when setting it.
- **Errors:** `400 OAUTH_RESPONSE_INVALID`; `400 OAUTH_STATE_INVALID`; `400 OAUTH_STATE_EXPIRED`; `400 OAUTH_BROWSER_BINDING_INVALID`. Handled denial and exchange failure redirect with the exact `303` result `denied` and `failed`, respectively.
- **Tables:** atomically consume transaction; after network exchange upsert installation, encrypt tokens/webhook URL, and upsert verified channel from response team ID, `authed_user.id`, and `incoming_webhook.channel_id`.
- **Transaction/side effects:** state+binding consume commits first; call `oauth.v2.access` outside SQL using HTTP Basic client authentication and the exact redirect URI; validate `ok`, app/team/user, actual scopes, and incoming-webhook host/shape. `incoming-webhook` is required; absent optional `app_mentions:read` disables inbound commands. Store in a second transaction. Never log/persist code.
- **Tests:** success, denial, missing/ambiguous code, expired/replayed state, wrong browser, exact redirect on exchange, missing scope/webhook fields, safe provider error, secret encryption, same install rotation, fixed redirect/cookie clearing.

### Endpoint 2 — List channels

- **Purpose:** show the current user's safe paired destinations.
- **Method/path/auth:** `GET /api/v1/notification-channels`; bearer required.
- **Path/query params and body:** no path/body. Optional non-null `kind` is one of `email|telegram|slack`; optional non-null `status` is one of `pending_verification|verified|disabled`; `limit` is an integer default `20`, range `1..100`; optional non-null `cursor` is 1–2,048 characters and follows the exact Phase 7 cursor policy, bound to both filters and `created_at DESC,id DESC`.
- **Success:** `200 {"items": array[NotificationChannelPublic], "next_cursor": string | null}` with at most `limit` items in the fixed order.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `422 CHANNEL_FILTER_INVALID`; `422 PAGE_LIMIT_INVALID`; `422 REQUEST_VALIDATION_FAILED` for cursor surface type/length/alphabet; `400 INVALID_CURSOR` for cursor semantic/schema/filter/order mismatch.
- **Tables/transaction/side effects:** owner-scoped read; read transaction only; none.
- **Tests:** owner isolation, filters, stable pagination, encrypted config never serialized, redaction formats.

### Endpoint 3 — Enable or disable a channel

- **Purpose:** pause/re-enable an unchanged verified destination.
- **Method/path/auth:** `PATCH /api/v1/notification-channels/{channel_id}`; owner required.
- **Params:** UUID path; no query.
- **Body:** exactly `{"enabled":boolean}`; non-null, unknown fields forbidden. Destination/label/secret changes are not patchable.
- **Success:** `200 NotificationChannelPublic`.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 CHANNEL_NOT_FOUND`; `409 CHANNEL_NOT_VERIFIED` when **either** boolean is submitted for a pending channel; `422 REQUEST_VALIDATION_FAILED`. Repeating the current enabled state for a verified/disabled channel is idempotent `200`.
- **Tables:** lock/update channel, append audit.
- **Transaction/side effects:** state/event commit once; queued work policy is explicit—disabling cancels not-started ordinary deliveries in the same transaction or makes worker skip them.
- **Tests:** disable/re-enable; pending `enabled=true` and pending `enabled=false` both return `409 CHANNEL_NOT_VERIFIED` and do not transition pending to disabled; other user hidden; concurrent patch; no delivery through disabled channel.

### Endpoint 4 — Delete/disconnect a channel

- **Purpose:** make credentials unusable while retaining safe audit history.
- **Method/path/auth:** `DELETE /api/v1/notification-channels/{channel_id}`; owner plus recent auth.
- **Params/body:** UUID path; no query/body.
- **Success:** `204`, empty body.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `403 RECENT_AUTH_REQUIRED`; owner-scoped `404 CHANNEL_NOT_FOUND`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID. Repeating deletion for the owner's tombstone is `204`.
- **Tables:** lock channel, erase ciphertext/provider target, set `deleted_at`/disabled status, cancel queued deliveries, consume challenges, audit. For the baseline's one-channel Slack installation, also disable it and insert encrypted durable credential-revocation work using Phase 6's revocation contract.
- **Transaction/side effects:** local disablement and any Slack revocation intent commit together; no provider call inline. The worker later calls Slack `auth.revoke`, retries known transient failure, and wipes its token copy on terminal completion.
- **Tests:** ciphertext removed, queued cancellation, running action gets cancel-requested without false reversal, repeat `204`, history safe, other user hidden, Slack revocation survives crash/provider outage and eventually wipes its token copy.

### Endpoint 5 — Create/rotate a verification challenge

- **Purpose:** prove possession of the exact pending destination.
- **Method/path/auth:** `POST /api/v1/notification-channels/{channel_id}/verification-challenges`; owner required.
- **Params:** UUID path; no query.
- **Body:** `{}` only; unknown fields forbidden.
- **Success:** `201 {"id":UUID,"channel_id":UUID,"method":"email_code"|"telegram_pairing","expires_at":datetime,"delivery_id":UUID|null,"pairing_code":string 43..128|null,"instruction":string 1..500}`. All keys are present. `delivery_id` is non-null only for email; `pairing_code` is non-null and appears once only for Telegram. Email code never appears through the API. Verified Slack channels return `409 CHANNEL_ALREADY_VERIFIED`.
- **Headers:** request ID and `Cache-Control: no-store`; explicitly omit `Location` because this phase defines no retrieve-challenge endpoint.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 CHANNEL_NOT_FOUND`; `409 CHANNEL_ALREADY_VERIFIED`; `409 CHANNEL_DISABLED`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID or a non-empty/unknown body; `429 CHALLENGE_RATE_LIMITED` with bounded `Retry-After`.
- **Tables:** consume old active challenge; insert digest/expiry plus encrypted secret only for email; email inserts a server-template verification delivery referencing the challenge, not plaintext code.
- **Transaction/side effects:** challenge and optional delivery commit once; worker sends later. Telegram has no outbound send.
- **Tests:** old challenge invalidated; digest/ciphertext not plaintext; code entropy/expiry; email delivery contains only challenge reference; ciphertext wiped after accepted send; pending ordinary delivery prohibited; Slack conflict; rate limit; Telegram code returned once; successful response has no `Location` header.

### Endpoint 6 — Verify an email code

- **Purpose:** atomically consume a valid email code. Telegram pairing is consumed by its authenticated provider event; Slack is verified by OAuth.
- **Method/path/auth:** `POST /api/v1/notification-channels/{channel_id}/verify`; owner required.
- **Params:** UUID path.
- **Body:** exactly `{"challenge_id":UUID,"code":"<six digits>"}` matching `^[0-9]{6}$`; non-null; reject whitespace changes rather than guessing.
- **Success:** `200 NotificationChannelPublic` with verified status/time.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 CHANNEL_NOT_FOUND`; owner/channel-scoped `404 CHALLENGE_NOT_FOUND`; `409 CHANNEL_ALREADY_VERIFIED`; `409 CHALLENGE_EXPIRED`; `409 CHALLENGE_CONSUMED`; `409 VERIFICATION_METHOD_MISMATCH` for Telegram/Slack; `422 CODE_INVALID`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID/null/unknown body fields; `429 TOO_MANY_ATTEMPTS`.
- **Tables:** lock channel/challenge; increment attempts or consume and verify; audit.
- **Transaction/side effects:** compare constant time; state changes commit once; none externally.
- **Tests:** correct, wrong, expired, replayed, superseded, concurrent submissions, max attempts, wrong owner, timing-safe helper, no digest/code in logs.

### Endpoint 7 — Queue a notification delivery

- **Purpose:** durably send one manual or internal event through an existing verified channel.
- **Method/path/auth:** `POST /api/v1/notification-deliveries`; bearer owner required.
- **Path/query params and headers:** no path/query; required single `Idempotency-Key` satisfying the exact 8–200 visible non-space ASCII phase contract.
- **Body:** `{"channel_id":UUID,"event_type":"manual","message":{"text":str(1..4000)}}`; no nulls/unknown fields. Ordinary clients cannot choose recipient, provider URL, purpose=`verification`, provider formatting, or reminder ID.
- **Success:** `202 NotificationDeliveryPublic`, status queued, status URL.
- **Headers:** `Location: /api/v1/notification-deliveries/{id}`, request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 CHANNEL_NOT_FOUND`; `409 CHANNEL_NOT_VERIFIED`; `409 CHANNEL_DISABLED`; `409 IDEMPOTENCY_KEY_REUSED`; `422 IDEMPOTENCY_KEY_INVALID`; `422 REQUEST_VALIDATION_FAILED` for the path/query/body contract; `429 USER_DELIVERY_QUOTA_EXCEEDED`.
- **Tables:** read/lock channel; insert delivery and audit.
- **Transaction/side effects:** one commit; no provider call in HTTP process.
- **Tests:** all channel kinds; unverified/disabled blocked; same key same row; changed payload conflict; user cannot target another channel; text limits/unknown/null; sensitive-template policy.

### Endpoint 8 — Retrieve delivery status

- **Purpose:** poll attempts and an honest provider outcome.
- **Method/path/auth:** `GET /api/v1/notification-deliveries/{delivery_id}`; owner required.
- **Params/body:** UUID path; no query/body.
- **Success:** `200 NotificationDeliveryDetail` with channel safe label, event type, status, attempt summaries, accepted/delivered timestamps, safe error; message text is omitted or redacted by retention policy.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 DELIVERY_NOT_FOUND`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID.
- **Tables/transaction/side effects:** owner-scoped delivery/attempt read; none.
- **Tests:** every state, other user hidden, `accepted` not mislabeled delivered, secret/raw provider responses absent, retry schedule visible safely.

### Endpoint 9 — Telegram webhook

- **Purpose:** authenticate and durably accept Telegram updates, including `/connect <nonce>` pairing and commands.
- **Method/path/auth:** `POST /api/v1/webhooks/telegram/{opaque_path_token}`; no bearer; configured opaque path plus `X-Telegram-Bot-Api-Secret-Token` required.
- **Params/body:** exact configured 43–128-character URL-safe path; secret header 1–256 characters; JSON Telegram Update subject to the exact 262,144-byte/JSON-structure/extracted-field limits above. A dedicated provider DTO uses `extra="ignore"` for evolution and retains only those listed fields.
- **Success:** `200 {"ok":true}` quickly for new or duplicate valid events. Do not reveal pairing result to Telegram response.
- **Headers:** request ID; `Content-Type: application/json`.
- **Errors:** `401 WEBHOOK_UNAUTHENTICATED` missing/wrong secret/path; `400 WEBHOOK_INVALID` malformed/oversize JSON. Invalid requests are not stored.
- **Tables:** insert webhook event unique on update ID; `/connect` locks/consumes challenge and verifies channel; other mapped text inserts inbound command.
- **Transaction/side effects:** verify header constant-time before parse; dedupe/store/pair in short transaction, then acknowledge. Command interpretation is worker work.
- **Tests:** wrong path/header; malformed/oversize; duplicate update returns 200 but one row; pairing correct/wrong/expired/replayed; chat bound to owner channel; ordinary command creates no task/tool side effect inline.

### Endpoint 10 — Slack Events webhook

- **Purpose:** authenticate and durably accept Slack URL verification/events/commands.
- **Method/path/auth:** `POST /api/v1/webhooks/slack/events`; no bearer; Slack HMAC headers required.
- **Params/body:** `X-Slack-Request-Timestamp` and `X-Slack-Signature` have the exact shapes above; raw JSON is capped at 262,144 bytes and all structure/text/challenge/ID limits above apply. Verify raw bytes before JSON parsing. Provider DTO ignores unknown fields but requires the bounded type and relevant IDs.
- **Success:** signed `url_verification` returns `200 {"challenge":"exact bounded challenge"}`; normal/duplicate valid event returns `200 {"ok":true}` quickly.
- **Headers:** request ID; `Content-Type: application/json`.
- **Errors:** `401 WEBHOOK_UNAUTHENTICATED` for missing/bad signature; `401 WEBHOOK_STALE` for timestamp outside the five-minute policy; `400 WEBHOOK_INVALID` for malformed/oversize input. Never return stack/provider payload.
- **Tables:** new normal event inserted by unique `event_id`; verified `(team_id, channel_id)` maps through the OAuth-created installation/channel. Only `app_mention` from the recorded installer Slack user creates an inbound command. URL verification is not a command.
- **Transaction/side effects:** HMAC-SHA256 constant-time comparison over Slack's exact base string; dedupe/store transaction only; async worker interprets.
- **Tests:** official signature fixture; one-byte body change; stale/future timestamp; signature checked before parse; URL challenge only after valid signature; retry headers/duplicate event; unmapped channel ignored safely; no inline tool execution.

### Endpoint 11 — Upload a voice command

- **Purpose:** durably accept bounded audio for later transcription and proposal generation.
- **Method/path/auth:** `POST /api/v1/voice-commands`; bearer required.
- **Params/body:** `multipart/form-data` with required `audio` file and optional text parts `language_hint` (BCP 47 allowlist, 2..35 ASCII characters) and `retention_hours` integer 1..72. Each text part is at most 64 bytes; at most three parts and one of each are accepted. Only these names; no JSON/null. Global limits are 15 MiB and 120 seconds; supported detected formats initially WAV, MP3, M4A/MP4 audio, or OGG/Opus. A configured adapter may advertise a tighter limit; the Google walkthrough below makes this endpoint enforce 10 MiB and 60 seconds before queuing.
- **Success:** `202 VoiceCommandPublic` with uploaded/queued status, detected metadata, retention deadline, no transcript yet.
- **Headers:** `Location: /api/v1/voice-commands/{id}`, request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; `400 MULTIPART_INVALID`; `413 AUDIO_TOO_LARGE`; `415 AUDIO_TYPE_UNSUPPORTED` for MIME/signature disagreement/unsupported codec; `422 AUDIO_INVALID`; `422 REQUEST_VALIDATION_FAILED` for bounded language/retention scalar fields; `429 VOICE_QUOTA_EXCEEDED`; `429 VOICE_STORAGE_QUOTA_EXCEEDED`.
- **Tables:** insert voice metadata/work/audit only after validated storage succeeds.
- **Transaction/side effects:** stream to randomized staging file while hashing/capping bytes; validate signature/duration; atomically move to controlled key; commit metadata. On failure delete only the explicit staging file. No transcription inline.
- **Tests:** valid fixtures each type; renamed executable/text; forged MIME; truncated/corrupt; duration/size boundary; unexpected part; filename traversal ignored; owner-only permissions/random name; DB-failure orphan cleanup; upload never read wholly into RAM.

### Endpoint 12 — Retrieve a voice command

- **Purpose:** poll processing, transcript, and linked exact tool proposals.
- **Method/path/auth:** `GET /api/v1/voice-commands/{voice_command_id}`; owner required.
- **Params/body:** UUID path; no query/body.
- **Success:** `200 VoiceCommandDetail` with status, safe transcript/language when ready, audio state/deletion deadline, and Phase 5 run summaries including decision URL/version/input hash. Never expose storage path or provider raw response.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 VOICE_COMMAND_NOT_FOUND`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID.
- **Tables:** voice row plus linked tool runs/definitions/decisions.
- **Transaction/side effects:** read only; none.
- **Tests:** each state, owner isolation, no transcript before ready, failed safe error, proposals exact and writes awaiting approval, storage key hidden.

### Endpoint 13 — Request audio deletion

- **Purpose:** remove retained bytes without necessarily erasing an allowed transcript/audit record.
- **Method/path/auth:** `DELETE /api/v1/voice-commands/{voice_command_id}/audio`; owner required.
- **Params/body:** UUID path; no query/body.
- **Success:** `202 VoiceCommandPublic` with `audio_state=delete_queued`; repeat returns the same safe state. Already deleted audio returns `202` with `audio_state=deleted`.
- **Headers:** request ID, `Cache-Control: no-store`.
- **Errors:** `401 INVALID_ACCESS_TOKEN`; owner-scoped `404 VOICE_COMMAND_NOT_FOUND`; `422 REQUEST_VALIDATION_FAILED` for malformed UUID. A running transcriber cooperatively observes deletion intent; it is not a reason to reject the request.
- **Tables:** lock voice row, set delete requested, enqueue/reuse deletion work, audit.
- **Transaction/side effects:** commit intent; storage worker idempotently deletes exact stored key then marks deleted. Automatic retention calls the same service.
- **Tests:** repeat, concurrent retention/manual request, deletion during processing policy, exact-key deletion, missing object still finalizes deleted, other user hidden, transcript retention policy independent.

No separate voice approval endpoint exists. The response links each proposal to the single Phase 5 `POST /api/v1/tool-runs/{id}/decisions` endpoint so voice cannot create a weaker parallel authorization path.

## 7. Ordered vertical build slices and stop gates

Build in this order. A **stop gate** means do not add the next provider while the named behavior is unproven; otherwise several queues and credentials will fail at once and teach you little.

### Slice 1 — Schema and fake contracts

1. Write the channel, challenge, delivery/attempt, webhook/command, Slack OAuth/installation, and voice tables in plain SQL.
2. Apply the ordered reminder migration from the exact refactor above: delivery tables first, then `next_trigger_attempt_at` backfill/non-null/index, then the partial uniqueness constraint. Replace the Phase 3 command only after the new schema exists.
3. Verify/preserve Phase 1's shared route-template helper in middleware and catch-all, confirm the explicit Telegram template/fixed fallback, disable raw Uvicorn/proxy access logs, and run the success/error/wrong-token/404 secret-canary tests **before** exposing Telegram.
4. Implement enums/state-transition unit tests, public serializers, owner-scoped repositories, and fake notification/storage/transcription ports. Do not add a provider SDK yet.

**Stop gate:** a database built from zero passes upgrade/downgrade/upgrade; every public response matches the normative vocabulary; constraints reject invalid states; fakes cover acceptance, permanent failure, `429`, pre-send failure, and response-lost uncertainty; no seeded Telegram path/query secret appears in any success, exception, or unmatched-route log.

### Slice 2 — One verified channel end to end

Implement email channel creation, Endpoint 5 challenge creation, a fake-email verification delivery worker, Endpoint 6 verification, channel list/enable/delete, then manual delivery queue/poll. Keep sending outside SQL and finalize with lease compare-and-set.

**Stop gate:** plaintext codes/addresses never enter logs; an unverified channel cannot receive ordinary text; duplicate idempotency keys converge; worker crash/expired-lease tests recover; disabled/deleted channels block a dispatch-time recheck.

### Slice 3 — Due reminders become deterministic deliveries

Implement Phase 3 create/PATCH/snooze writes to keep `next_trigger_attempt_at` atomic, then implement `ReminderDeliveryService.materialize_due` exactly as specified: lock the schedule, repair a stale future due key, lock the complete default target set, insert deterministic rows, and only then trigger. Run it through a command/worker loop. Test earlier/later PATCH and its race with a claim, then zero targets before one and three targets; finally inject a failure between the first and second insert.

**Stop gate:** zero targets leave the schedule retriable and untriggered; an injected failure commits neither trigger nor partial rows; repeated/concurrent claims yield one complete row per verified/enabled channel; provider failures change delivery state but never re-trigger the schedule.

### Slice 4 — Telegram pairing and authenticated webhook intake

Add Telegram's long nonce, secret/path verification, bounded DTO, webhook dedupe, `/connect`, and command storage. Only after intake tests pass, connect the command worker to Phase 4 interpretation and Phase 5 proposals.

**Stop gate:** malformed/oversize/unverified updates have no row or tool side effect; a valid duplicate returns quickly but creates one event/command; commands create proposals, and a write remains `awaiting_approval`.

### Slice 5 — Slack OAuth before Slack Events

Implement S1/S2 with a fake token exchange, browser-bound one-use state, exact callback bounds, scope/identity validation, encrypted installation/webhook material, and verified team/channel mapping. Add outbound Slack adapter contract tests. Only then enable Endpoint 10 and map signed `app_mention` events through that installation.

**Stop gate:** wrong browser/replay/missing incoming webhook cannot install; reinstall rotates secrets without duplicate channels; a one-byte signed-body change fails; URL verification echoes only a signed bounded challenge; events from another team/channel/user create no command.

### Slice 6 — Voice with fake transcription

Stream upload to controlled staging, validate actual media and duration, move by a random database-owned key, queue fake transcription, store bounded provenance, interpret, link exact Phase 5 proposals, and add idempotent manual/retention deletion.

**Stop gate:** corrupt/renamed/oversize files clean up; no upload is buffered wholly in RAM; deletion races converge; a fake transcript that requests destruction cannot approve its own bound run.

### Slice 7 — Optional real adapters and operational proof

Run contract suites against local HTTP/SMTP fakes first. Add one real outbound channel and the optional Google transcription adapter below behind configuration. Live smoke tests use dedicated non-sensitive accounts/audio and are excluded from default pytest/CI.

**Final gate:** queue age, attempt outcome, rate-limit, dead-letter, outcome-unknown, invalid-webhook, audio-retention, and secret-redaction observability exists; restart each worker mid-attempt and explain the recovered state before proceeding to Phase 8.

## 8. Pairing flows

### Email

Create pending channel -> create challenge -> worker sends a fixed “verification code” template only -> user posts code -> channel verified. Verification delivery is allowed through pending state only for this exact server-owned template. Do not allow user text in it.

### Slack

Start S1, let Slack's `incoming-webhook` OAuth scope show its channel picker, then finish S2. The signed token-exchange response supplies team, installer, and selected channel identities plus the secret webhook URL; the callback creates the verified mapping. Configure the Slack app's Events API for `app_mention` and route it to Endpoint 10. Missing optional `app_mentions:read` disables inbound commands while outbound notifications may remain available.

### Telegram

Create pending channel without chat ID -> issue long pairing nonce once -> user sends `/connect nonce` to the bot -> Telegram posts a webhook carrying the chat -> valid secret header plus valid unconsumed nonce binds the chat and verifies the channel. Do not trust a chat ID typed into your API.

## 9. Provider ports and limited snippets

Make outcome strength explicit:

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderAcceptance:
    provider_message_id: str | None
    provider_request_id: str | None


class NotificationSender(Protocol):
    def send(self, *, destination: object, text: str) -> ProviderAcceptance: ...
```

The adapter either returns known acceptance or raises a classified exception containing no secret. It does not call the repository.

Slack verification must use the raw bytes:

```python
import hashlib
import hmac


def valid_slack_signature(
    *, signing_secret: str, timestamp: str, raw_body: bytes, supplied: str
) -> bool:
    base = b"v0:" + timestamp.encode("ascii") + b":" + raw_body
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", supplied)
```

The caller parses the timestamp safely, rejects clock skew beyond policy **before** doing business work, and still uses constant-time comparison. Do not serialize parsed JSON and sign it; whitespace/key order would change.

Stream uploads instead of calling `await audio.read()` once:

```python
import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


async def stage_upload(upload: UploadFile, root: Path, max_bytes: int) -> tuple[Path, int, str]:
    path = root / f"stage-{uuid4().hex}.bin"
    total = 0
    digest = hashlib.sha256()
    with path.open("xb") as target:
        while chunk := await upload.read(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("audio too large")
            digest.update(chunk)
            target.write(chunk)
    return path, total, digest.hexdigest()
```

The real storage adapter sets restrictive permissions and owns cleanup in `finally`. The service validates detected type and duration before moving from staging. It never uses `upload.filename`, and deletion accepts a database-owned key only—not a caller path.

### Optional real adapter — Google Cloud Speech-to-Text V2

Keep `FakeTranscriber` as the default. This optional slice demonstrates one real adapter without making cloud credentials or a network a test dependency. At this guide's update, the official Python reference is `google-cloud-speech` 2.40.0 and Google documents the V2 model identifier `chirp_3`. Pin the optional dependency so a future SDK change is deliberate:

```toml
[project.optional-dependencies]
google-speech = ["google-cloud-speech==2.40.0"]
```

Install with `pip install -e '.[google-speech]'`. Enable the Speech-to-Text API in a dedicated Google Cloud project and use Application Default Credentials locally (`gcloud auth application-default login`); in deployment, attach a least-privilege workload identity. Never download a service-account key into the repository.

Add exact settings:

```dotenv
APP_TRANSCRIPTION_PROVIDER=fake
APP_GOOGLE_CLOUD_PROJECT=CHANGE_ME
APP_GOOGLE_SPEECH_LOCATION=us
APP_GOOGLE_SPEECH_MODEL=chirp_3
APP_GOOGLE_SPEECH_DEFAULT_LANGUAGE=en-IN
APP_GOOGLE_SPEECH_ALLOWED_LANGUAGES=en-IN,en-US
APP_GOOGLE_SPEECH_TIMEOUT_SECONDS=45
```

`fake` is valid everywhere; `google_cloud_speech` is valid only when all Google settings pass startup validation. The configured project/location/model/language are server-owned, never request fields. Pin `chirp_3`; do not interpret “latest” at runtime or silently fall back. Before each planned dependency/model upgrade, check the official [Chirp 3 model page](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3), [model comparison](https://docs.cloud.google.com/speech-to-text/docs/transcription-model), and [locations table](https://docs.cloud.google.com/speech-to-text/docs/locations). The selection rule is exact: use `chirp_3` only when those current tables list it for the configured location and every allowed language; otherwise fail configuration and have an operator choose and contract-test a currently documented model. The baseline sends the validated hint when allowlisted, otherwise the configured default—never an attacker-supplied arbitrary language.

Google's synchronous `Recognize` limit is the earlier of 10 MiB or 60 seconds. Advertise those as adapter capabilities and make Endpoint 11 reject a larger upload/duration **before queueing** when this provider is selected, even though local fake/storage limits are 15 MiB/120 seconds. Do not quietly switch to batch: V2 batch recognition requires a Cloud Storage URI and introduces a separate upload, IAM, location, deletion, and outcome design. Add it in a later explicit slice if needed.

Keep the adapter free of repositories and return a provider-neutral value:

```python
from dataclasses import dataclass

from google.api_core.client_options import ClientOptions
from google.cloud import speech_v2


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    detected_language: str | None
    provider: str
    model: str
    provider_request_id: str | None


class GoogleSpeechTranscriber:
    max_bytes = 10 * 1024 * 1024
    max_duration_ms = 60_000

    def __init__(
        self, *, project: str, location: str, model: str, timeout_seconds: float
    ) -> None:
        endpoint = f"{location}-speech.googleapis.com"
        self.client = speech_v2.SpeechClient(
            client_options=ClientOptions(api_endpoint=endpoint)
        )
        self.recognizer = f"projects/{project}/locations/{location}/recognizers/_"
        self.model = model
        self.timeout_seconds = timeout_seconds

    def transcribe(self, *, audio: bytes, language_code: str) -> TranscriptionResult:
        if len(audio) > self.max_bytes:
            raise ValueError("adapter byte limit exceeded")
        config = speech_v2.RecognitionConfig(
            auto_decoding_config=speech_v2.AutoDetectDecodingConfig(),
            language_codes=[language_code],
            model=self.model,
            features=speech_v2.RecognitionFeatures(
                enable_automatic_punctuation=True
            ),
        )
        response = self.client.recognize(
            request=speech_v2.RecognizeRequest(
                recognizer=self.recognizer,
                config=config,
                content=audio,
            ),
            timeout=self.timeout_seconds,
        )
        text = "\n".join(
            result.alternatives[0].transcript
            for result in response.results
            if result.alternatives
        ).strip()
        languages = {result.language_code for result in response.results if result.language_code}
        detected = next(iter(languages)) if len(languages) == 1 else None
        return TranscriptionResult(
            text=text,
            detected_language=detected,
            provider="google_cloud_speech",
            model=self.model,
            provider_request_id=None,
        )
```

The service, not this snippet, verifies `duration_ms <= 60_000`, the allowlisted language, nonempty output, and the 100,000-character transcript limit. If joined output is too large, store no partial transcript and fail `TRANSCRIPTION_OUTPUT_LIMIT_EXCEEDED`. A null provider request ID is honest when the client response does not expose one; do not invent it.

Map SDK failures at the adapter boundary without copying provider text: `InvalidArgument` -> permanent `TRANSCRIPTION_INPUT_INVALID`; `Unauthenticated`/`PermissionDenied` -> permanent `TRANSCRIPTION_PROVIDER_AUTH_FAILED` plus operator alert; `NotFound`/`FailedPrecondition` -> permanent `TRANSCRIPTION_CONFIGURATION_INVALID`; `ResourceExhausted` -> retryable `TRANSCRIPTION_RATE_LIMITED`, honoring bounded provider retry metadata or using 60 seconds; and `DeadlineExceeded`/`Unavailable`/`InternalServerError` -> retryable `TRANSCRIPTION_PROVIDER_UNAVAILABLE` with Phase 5 exponential backoff/jitter. Transcription has no remote write, so a lost response may be retried within `max_attempts`; audio deletion intent is rechecked before every attempt. Store only the safe code, attempt time, provider/model, and request ID if present.

Privacy is a design input. Google states that synchronous audio is processed in memory and customer audio/transcripts are not used for model improvement unless data logging is explicitly enabled; metadata can still be logged. Leave data logging/opt-in disabled, choose an allowed region deliberately, grant only recognition permission, never log audio/transcript, and apply your local audio/transcript retention policy after the call. Review the current [data usage FAQ](https://docs.cloud.google.com/speech-to-text/docs/v1/data-usage-faq), [data logging terms](https://docs.cloud.google.com/speech-to-text/docs/v1/data-logging), and [audit logging](https://docs.cloud.google.com/speech-to-text/docs/audit-logging) with your own privacy requirements before personal audio.

Manual smoke procedure:

1. Follow Google's [setup](https://docs.cloud.google.com/speech-to-text/docs/setup) and [Python client](https://docs.cloud.google.com/speech-to-text/docs/libraries) pages; enable billing/API and create ADC for a dedicated development project.
2. Verify the configured location/model/languages against the current official tables; set provider to `google_cloud_speech` and start the API/worker. Startup must fail closed on missing config.
3. Upload a synthetic, non-sensitive WAV shorter than ten seconds. Poll Endpoint 12; expect provider/model provenance, a bounded transcript, and only Phase 5 proposals.
4. Check logs contain request/voice IDs and safe codes but no audio, transcript, credential, or provider body. Request deletion and verify the exact local object becomes `deleted`.
5. Test a fake `ResourceExhausted`/timeout through a mocked client. Keep a real live smoke marked `@pytest.mark.live_google_speech` and excluded from default CI.

Default unit/integration tests inject `FakeTranscriber`; contract tests inject a fake Google client response/exception into this adapter. No default test reads ADC, performs DNS, bills a project, or sends audio externally.

## 10. Delivery, webhook, and voice workers

Reuse Phase 5 claim/lease/attempt mechanics. Keep each database transaction short:

1. claim one due row with `FOR UPDATE SKIP LOCKED`, set lease, insert attempt, commit;
2. load/decrypt only required destination;
3. call adapter outside transaction with connect/read/total timeout;
4. finalize using run ID plus lease owner compare-and-set.

Failure policy:

- Provider `429`: parse bounded `Retry-After`, schedule no earlier than it requests, add jitter, do not consume CPU in sleep.
- Connect failure before request bytes: retryable under bounded attempts.
- Provider `5xx`: retry only according to provider operation semantics.
- Valid provider `4xx` for bad target/auth: permanent; disable or flag channel when appropriate.
- Accepted response with ID: `accepted`; provider receipt later may advance to `delivered`.
- Timeout/lost response after request may have arrived: reconcile if possible; otherwise `outcome_unknown`, not automatic retry.
- Max known-safe retries exhausted: `dead_letter`, visible to user/operator; a manual retry creates a new auditable delivery or explicit attempt according to policy.

The webhook endpoint does **not** call a model. A command worker maps a verified provider identity to a verified channel/user, normalizes bounded text, calls the Phase 4 interpreter, validates tool proposals through Phase 5, and creates tool runs. Risk policy determines queued reads versus awaiting-approval writes. Provider text cannot supply an access token or approval.

The transcription worker fetches a database-owned object key, checks retention/deletion state, calls `TranscriberPort`, stores bounded transcript/provenance, deletes temporary plaintext, then queues interpretation. Treat transcript and model output as untrusted. If a destructive phrase is proposed, its exact tool version/input hash is waiting for the same recent human approval as typed input.

## 11. Testing strategy

### Unit tests

- channel state transitions and destination redaction/fingerprint;
- Slack OAuth state/browser binding, scope allowlist, response validation, secret redaction, and fixed redirects;
- HMAC challenge digest, expiry, rotation, attempt/rate limits;
- delivery transition/retry classifier, `Retry-After` cap, jitter with fake randomness;
- Telegram secret and Slack raw-body signature/timestamp verification;
- provider DTO extraction with unknown fields;
- audio signature/MIME/duration/size matrix and random key generation;
- transcript retention and proposal rules; no write proposal auto-approved.

### PostgreSQL integration tests

- duplicate channel/challenge/delivery races converge under constraints;
- two delivery workers claim different rows; expired leases recover;
- reminder trigger plus all delivery rows are one transaction;
- webhook event duplicate insert creates one command;
- Telegram pairing consumes nonce once and binds exact chat;
- Slack callback state is one-use and upserts one verified team/channel mapping;
- user isolation for channels, deliveries, webhooks' mapped resources, and voice;
- deletion request and retention worker race converge;
- migration round trip and every database check constraint.

### Fake-provider and contract tests

Create fake Telegram, email, Slack, object storage, and transcription adapters. Shared scenarios: accepted with/without ID, definitive 400, auth revoked, 429/Retry-After, 500, connection failure, timeout-before-send, accepted-then-response-lost. Assert service status and retry time—not adapter internals.

HTTP contract tests verify Telegram method/body and secret-safe URL handling; Slack webhook JSON and error/status mapping; email `EmailMessage`/SMTP envelope separation and deterministic Message-ID; transcription content type/timeout/response bounds. Use an HTTP mock transport or local fake server, never live networks in default pytest.

### Security tests

Feed invalid signatures, stale Slack timestamps, huge bodies, duplicate events, path traversal filenames, MIME polyglots/renamed fixtures, malformed media metadata, decompression/parser timeouts, unexpected multipart parts, HTML/script notification text, and another user's UUID. Assert rejection, bounded time/memory, no unsafe file outside staging, no secret logs, and no side effect.

## 12. Manual exercise and debugging

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/notification-channels \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"email","label":"Personal","destination":{"address":"me@example.test"}}'

curl -i -X POST "http://127.0.0.1:8000/api/v1/notification-channels/$AIW_CHANNEL_ID/verification-challenges" \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

curl -i -X POST http://127.0.0.1:8000/api/v1/voice-commands \
  -H "Authorization: Bearer $AIW_ACCESS_TOKEN" \
  -F "audio=@tests/fixtures/audio/short-valid.wav;type=audio/wav" \
  -F "language_hint=en-IN" \
  -F "retention_hours=1"
```

Use a local fake email inbox and fake adapters first. Never put a Slack webhook URL, Telegram token, or verification code in shell examples, source, screenshots, or logs.

Debug a delivery: find request ID -> delivery ID -> attempt -> worker/lease -> adapter classification -> next attempt/final state. Compare database `now()` with lease/retry times. A growing `retry_scheduled` queue suggests rate limiting or worker capacity; growing `outcome_unknown` suggests missing provider reconciliation or too-short timeouts.

Debug a webhook: record status and request ID -> verify the exact raw bytes were read once -> inspect timestamp/secret verification result (not secret) -> query unique provider event ID -> command mapping -> tool run. If Slack signatures suddenly fail behind middleware, check whether something decoded/re-encoded the body before verification.

Debug voice: request ID -> staged-file cleanup -> detected signature/codec -> duration -> storage key existence -> transcription attempt -> transcript bounds -> tool proposals. Never dump raw audio/transcript into ordinary logs.

## 13. Security, privacy, and reliability checklist

- Verify destination before normal use; reverify every changed destination.
- Encrypt Slack webhook URLs, Telegram bot token/config, email provider credentials, and any destination considered sensitive.
- Telegram uses both opaque path and secret header; Slack uses signing secret, raw body, timestamp replay window, and constant-time compare.
- Deduplicate provider events before interpretation and return fast `2xx` for valid duplicates.
- Never construct outbound URLs from untrusted input beyond strict documented provider hosts; block SSRF/private networks.
- Do not include sensitive memory, email, calendar, or transcript content in notification templates by default.
- Bound text, queue depth, attempts, retry delay, provider concurrency, upload bytes/duration, transcript size, storage quota, and retention.
- `accepted` does not mean read; unknown does not mean failed; cancellation after send starts does not prove nothing happened.
- Sanitize provider errors; metrics/logs use IDs, state, latency, and safe codes.
- Store audio outside served/static directories under randomized keys and restrictive permissions; scan/validate, auto-delete, and provide user deletion.
- Provider transcription retention/privacy terms are reviewed before real data; avoid live provider tests with personal audio.
- Inbound text/transcripts only propose Phase 5 tools. They cannot approve themselves or bypass owner scope.

## 14. Exercises and checkpoint

Exercises:

1. Pair email/Telegram with challenges and Slack with fake OAuth. Prove ordinary delivery fails before verification and succeeds after it.
2. Rotate a challenge; try old, wrong, expired, replayed, and concurrent correct codes. Explain every status.
3. Deliver the same reminder to three channels. Force accepted, retry, and permanent failure; show the reminder remains triggered once while outcomes differ.
4. Return Slack/Telegram `429` with different Retry-After values and prove workers schedule rather than sleep.
5. Simulate provider acceptance followed by connection loss. Explain why `outcome_unknown` blocks blind retry.
6. Send one Telegram update and one Slack event twice. Show one webhook event, one command, and one set of tool proposals.
7. Change one byte in Slack's raw body after signing and reject it. Re-serialize identical JSON with different whitespace and explain why its signature differs.
8. Upload a text file renamed `.wav`, a valid file with a false MIME, an oversized stream, a corrupt header, and a path-traversal filename. Verify safe rejection/cleanup.
9. Force DB commit failure after staging; show the exact staged object is removed without broad deletion.
10. Fake the transcript “delete all tasks.” Confirm the proposed destructive run is awaiting approval, alter its input, and fail the approval binding.
11. Request audio deletion while interpretation remains. Verify bytes disappear while the allowed transcript/audit retention policy is explicit.
12. Write an incident note for a leaked Slack webhook URL: revoke/replace, delete ciphertext, identify logs, reverify channel, and prevent recurrence.

Checkpoint:

- Email, Slack, and Telegram destinations have tested verification flows; unverified/disabled targets cannot carry ordinary notifications.
- Reminder trigger and delivery creation are atomic; delivery workers lease/retry/dead-letter correctly with fakes.
- Telegram/Slack webhook authentication, stale replay rejection, deduplication, and fast acknowledgement pass security tests.
- Inbound messages result only in stored commands and Phase 5 proposals.
- Voice upload streams safely, validates real media, stores metadata not bytes in PostgreSQL, transcribes through a fake/contract adapter, applies retention/deletion, and produces exact tool proposals.
- At least one real channel and one real transcription adapter can be smoke-tested manually with dedicated non-sensitive accounts/data; default tests require no network.
- All migrations rebuild from zero and every endpoint card has executable API tests.

## What you should be able to explain after Phase 7

Explain: schedule state versus delivery state; verification challenge; low-entropy code HMAC; provider acceptance versus human delivery; outbox/durable delivery; lease/attempt/retry/backoff/jitter/dead letter; `429` and Retry-After; outcome unknown; webhook authentication versus HTTPS; raw-body HMAC; replay window; deduplication; quick acknowledgement; provider DTO evolution; SSRF; multipart streaming; MIME declaration versus file signature; duration validation; object storage key versus filename; retention/deletion; transcription confidence; and why voice or webhook text can propose but never authorize an action.

## Official primary documentation

- [Telegram Bot API](https://core.telegram.org/bots/api) — `setWebhook`, `secret_token`, Update IDs, `sendMessage`, API responses, and retry information.
- [Telegram webhook guide](https://core.telegram.org/bots/webhooks) — HTTPS endpoint setup and certificate behavior.
- [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/) — raw body, `v0` HMAC base string, timestamp window, constant-time comparison.
- [Slack installation with OAuth](https://docs.slack.dev/authentication/installing-with-oauth/) and [`oauth.v2.access`](https://docs.slack.dev/reference/methods/oauth.v2.access/) — state, exact redirects, scopes, exchange, team/user identity, tokens, and revocation.
- [Slack Events API](https://docs.slack.dev/apis/events-api/) and [HTTP request URLs](https://docs.slack.dev/apis/events-api/using-http-request-urls/) — URL verification, event IDs, acknowledgements, and retries.
- [Slack incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks) — secret URL behavior, posting, and error classes.
- [Slack Web API rate limits](https://docs.slack.dev/apis/web-api/rate-limits/) — `429`, `Retry-After`, and method/workspace limits.
- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/) — `UploadFile` and multipart semantics.
- [Google Cloud Speech-to-Text synchronous recognition](https://docs.cloud.google.com/speech-to-text/docs/sync-recognize), [Chirp 3](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3), and [Python client reference](https://cloud.google.com/python/docs/reference/speech/latest) — short-audio limits, model/location/language support, V2 request types, and errors for the optional real adapter.
- [`google-cloud-speech` release files](https://pypi.org/project/google-cloud-speech/) — verify the explicitly pinned client release and hashes during dependency review.
- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) — extension/signature, generated names, limits, storage isolation, and scanning defenses.
- [Python `email` examples](https://docs.python.org/3/library/email.examples.html) and [`smtplib`](https://docs.python.org/3/library/smtplib.html) — structured messages and SMTP transport if that is your email adapter.
- [HTTP `429 Too Many Requests`](https://www.rfc-editor.org/rfc/rfc6585.html#section-4) and [`Retry-After`](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after) — client retry semantics.
- [PostgreSQL row locking and `SKIP LOCKED`](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE) — the queue-claim primitive reused from Phase 5.

Before moving to persistent memory, prove that private data does not leak through notification content, logs, webhook storage, audio files, or transcripts. Reliability without privacy is not a successful personal workspace.
