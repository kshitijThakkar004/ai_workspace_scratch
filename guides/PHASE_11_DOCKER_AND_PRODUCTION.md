# Phase 11 handbook — Docker, delivery, operations, and recovery

Docker comes last because it packages behavior you already understand. This phase makes the locally working FastAPI/PostgreSQL application reproducible on a clean machine, then adds controlled migrations, a separate worker, health semantics, CI, observability, backups, restore drills, deployment, and recovery.

## 1. Outcome, prerequisites, and non-goals

By the end:

- One digest-pinned application image runs as a non-root user.
- Compose starts a pinned PostgreSQL image with a named volume, runs migrations once, then starts API and worker from the same application image.
- Liveness answers without dependencies; readiness verifies database reachability and compatible Alembic revision.
- CI builds from clean inputs and runs unit, PostgreSQL integration, migration, static, and container checks.
- Logs/metrics/traces and alert candidates describe HTTP and durable-job behavior safely.
- You can back up, restore into a new database, migrate, and smoke-test it from a written runbook.
- A deployment/recovery checklist states who/what runs migrations and how to react to failure.

Prerequisites:

- Phases 0–10 run locally without Docker and pass against PostgreSQL.
- A committed `requirements.lock` with hashes captures the reviewed runtime graph, and a committed `requirements-dev.lock` with hashes extends it for CI/development. The exact workflow is defined below.
- The Phase 8 consolidated `app.workers.main` entry point dispatches the Phase 5–10 handlers, whose durable contracts handle leases, idempotency, retry, cancellation, and graceful shutdown.
- Configuration is loaded through validated Pydantic settings; no secrets are committed.
- You understand Alembic upgrades and have tested a blank database.

Non-goals:

- No Kubernetes requirement, home-grown secret manager, or premature Redis.
- No claim that Compose itself is high availability.
- No database inside the API container.
- No automatic migration from every API replica.
- No “backup” that is only the live Docker volume.

## 2. Production shape and responsibilities

```mermaid
flowchart TB
    LB["TLS terminator / platform router"] --> API1["API container"]
    LB --> API2["optional API replica"]
    MIG["one-shot migration job"] --> DB[("PostgreSQL durable storage")]
    API1 --> DB
    API2 --> DB
    W["worker container"] --> DB
    API1 & API2 & W --> EXT["allowlisted external providers"]
    API1 & API2 & W --> OBS["logs / metrics / traces"]
    B["backup system"] --> DB
    B --> STORE["encrypted separate backup storage"]
```

One image, several commands:

- **Migration:** `alembic upgrade head`, runs once per release and exits.
- **API:** Uvicorn/FastAPI, stateless apart from external object storage and PostgreSQL.
- **Worker:** same code/version, different entry command, consumes durable rows.
- **PostgreSQL:** separate official image locally; normally managed durable database in production.

This keeps schema and code releases coherent. “One image” does not mean one process. If a platform handles replication, prefer one server process per container and replicate containers; a single-host Compose deployment may choose multiple server workers only after measuring memory and shutdown behavior.

## 3. Image pinning without invented digests

Tags are mutable. Select supported exact Python/PostgreSQL patch tags, inspect their current official-image digests, review release notes, and commit `tag@sha256:<64 hex>` references to a deployment manifest. This handbook does not paste a soon-stale or fabricated digest.

Example discovery commands:

```bash
docker buildx imagetools inspect python:3.13.7-slim-bookworm
docker buildx imagetools inspect postgres:17.6-bookworm
```

Those tags are examples, not a current-version promise. At implementation time choose supported versions compatible with your dependencies and platform. Record `PYTHON_IMAGE` and `POSTGRES_IMAGE` as full digest-pinned references. Dependabot/Renovate or a scheduled review can propose digest updates; CI and a human-reviewed change make them deliberate.

## 4. Files and why they exist

```text
Dockerfile                         # deterministic application-image recipe
.dockerignore                      # excludes secrets, VCS, caches, local data
requirements.lock                 # exact hashed runtime dependency graph
requirements-dev.lock             # exact hashed runtime + CI/development graph
compose.yaml                       # local multi-process topology and named volume
.env.compose.example               # names/non-secret examples; real file ignored
deploy/
├── image-versions.env.example     # documents required digest-pinned variables
├── runbooks/
│   ├── deploy.md                  # migration/rollout/verify/rollback decisions
│   ├── restore.md                 # target validation, restore, migrate, smoke
│   └── incident.md                # observe, contain, communicate, recover
└── dashboards/                    # versioned metric/alert definitions later
app/ops/
├── health.py                      # live/ready services, no feature logic
├── storage.py                     # bounded voice-volume startup/readiness probe
├── metrics.py                     # bounded-cardinality instrumentation
├── logging.py                     # structured redaction/correlation
└── restore_smoke.py               # read-only invariants against a named restore DB
.github/workflows/ci.yml           # clean automated verification (or CI equivalent)
```

Do not put production credentials in any of these. `.env.compose.example` documents variable names; `.env.compose` is local-only and gitignored. Production receives secrets from its secret mechanism.

## 5. Build the non-root application image

First make operational tools declared dependencies rather than relying on laptop-global commands. If you implement `/metrics`, add `prometheus-client>=0.21,<1` to runtime dependencies. Add `ruff>=0.12,<1`, `mypy>=1.15,<2`, and `pip-tools>=7.4,<8` to the existing development extra. From a clean supported Python environment, compile `requirements.lock` from the runtime dependencies and `requirements-dev.lock` from the development extra with hashes; commit both and regenerate them only through review. Verify the latter with `python -m pip install --require-hashes -r requirements-dev.lock`. A `pip freeze` list is useful diagnostics, but it is not this resolver workflow.

One concrete `pip-tools` workflow is:

```bash
python -m piptools compile --all-build-deps --allow-unsafe --generate-hashes --output-file=requirements.lock pyproject.toml
python -m piptools compile --all-build-deps --allow-unsafe --extra=dev --generate-hashes --output-file=requirements-dev.lock pyproject.toml
python -m pip install --require-hashes -r requirements-dev.lock
```

`--all-build-deps` includes the static and dynamically reported wheel/editable build requirements, while `--allow-unsafe` prevents required packaging tools such as `setuptools` from being silently omitted from the lock. That allows the project install below to disable PEP 517 build isolation rather than resolve another dependency graph. Run compilation with the exact supported Python minor version used by the image and CI because environment markers can change the resolved graph. If the selected resolver emits a combined development lock rather than an extending lock, that is acceptable as long as the commands, Python target, indexes, and review/update process are documented and CI verifies hashes.

Those two commands describe the fake-provider baseline. If a production deployment enables an optional real-provider extra, include every selected production extra in **both** compilation commands (for example, add `--extra=google-speech` alongside `--extra=dev` in the development command), review the resulting lock diff, and test startup in that exact profile. Never install an adapter ad hoc after the hashed lock or let runtime configuration select code whose dependency is absent.

Use exec-form `CMD` so signals reach the server and graceful shutdown/lifespan handlers run. A required build argument prevents accidental unpinned defaults:

```dockerfile
# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 10001 -m 0700 /var/lib/ai-workspace/voice

WORKDIR /app

COPY --chown=10001:10001 pyproject.toml requirements.lock alembic.ini ./
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 alembic ./alembic

RUN python -m pip install --require-hashes -r requirements.lock \
    && python -m pip install --no-build-isolation --no-deps .

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

If dependencies need compilers, use a builder stage to create wheels and copy only wheels into the runtime stage. Do not install a compiler, editor, curl, or test tools in runtime without a reason. Ensure package metadata includes migrations if your layout differs; build and run `alembic heads` inside the image to prove they are present.

Suggested `.dockerignore`:

```text
.git
.venv
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
.env
.env.*
!.env.compose.example
*.pem
*.key
backups
postgres-data
tests
```

Excluding tests shrinks the runtime context, but CI must run tests before/alongside building. Do not exclude a file the package build actually needs. Check image contents, layer history, user ID, vulnerability scan, and start/shutdown before calling it done.

Create `.env.compose` from an ignored local template. Use real digest values and a non-production password:

```dotenv
PYTHON_IMAGE=python:3.13.7-slim-bookworm@sha256:<replace-with-reviewed-64-hex-digest>
POSTGRES_IMAGE=postgres:17.6-bookworm@sha256:<replace-with-reviewed-64-hex-digest>
APP_IMAGE_TAG=dev
APP_ENVIRONMENT=development
APP_LOG_LEVEL=INFO
APP_VOICE_STORAGE_ROOT=/var/lib/ai-workspace/voice
POSTGRES_DB=ai_workspace
POSTGRES_USER=ai_workspace
POSTGRES_PASSWORD=<replace-with-local-random-password>
APP_DATABASE_URL=postgresql+psycopg://ai_workspace:<same-url-encoded-password>@db:5432/ai_workspace
APP_JWT_SECRET=<replace-with-long-random-local-secret>
```

Angle-bracket values are instructions, not runnable values. Generate and replace them before starting. If the password contains URL-special characters, URL-encode the password portion or build the DSN safely in settings.

## 6. Compose: database, migration, API, worker, volume

Use required environment substitutions for digest-pinned images. The local database is intentionally not published to the host; use `docker compose exec db psql` or add a loopback-only development override when needed.

```yaml
name: ai-workspace

x-app: &app
  image: ai-workspace-api:${APP_IMAGE_TAG:-dev}
  build:
    context: .
    args:
      PYTHON_IMAGE: ${PYTHON_IMAGE:?Set a digest-pinned PYTHON_IMAGE}
  env_file:
    - .env.compose

services:
  db:
    image: ${POSTGRES_IMAGE:?Set a digest-pinned POSTGRES_IMAGE}
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-ai_workspace}
      POSTGRES_USER: ${POSTGRES_USER:-ai_workspace}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 10s

  migrate:
    <<: *app
    command: ["alembic", "upgrade", "head"]
    restart: "no"
    depends_on:
      db:
        condition: service_healthy

  api:
    <<: *app
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      APP_VOICE_STORAGE_ROOT: /var/lib/ai-workspace/voice
    volumes:
      - voice_audio:/var/lib/ai-workspace/voice
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 10s

  worker:
    <<: *app
    command: ["python", "-m", "app.workers.main"]
    environment:
      APP_VOICE_STORAGE_ROOT: /var/lib/ai-workspace/voice
    volumes:
      - voice_audio:/var/lib/ai-workspace/voice
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    stop_grace_period: 30s

volumes:
  postgres_data:
  voice_audio:
```

The two named volumes preserve database and Phase 7 voice audio when a container is replaced. `docker compose down -v` destroys both; never run that casually. The Dockerfile creates the mountpoint as UID/GID 10001 with mode `0700`; Docker copies those ownership/mode metadata into a newly created empty named volume, so API and worker can use it while remaining non-root. Assert this on the actual Docker/Compose platform—do not “fix” permission failures with `chmod 777`, root API/worker, `/tmp`, or writable `/app`.

This is a **single-host baseline**. API writes a staged file under `/var/lib/ai-workspace/voice`, fsyncs/closes it, atomically renames to the final opaque storage key, and only then commits/updates the PostgreSQL audio row; the worker reads/deletes the same mount. Database paths are relative opaque keys, never arbitrary absolute/client paths. On write failure, no ready DB row is committed. If the DB commit fails after rename, enqueue/reconcile the orphan by its content hash/storage key. Deletion first transactionally marks `delete_pending`, the worker removes the file idempotently, then marks `deleted`; a missing file is success only when hash/key/state match. A bounded reconciler reports/removes old unreferenced files and flags ready rows with missing/hash-mismatched files. This prevents silent database/audio divergence without pretending filesystem and PostgreSQL form one atomic transaction.

A named volume is local durability, not shared multi-host storage or a backup. Do not scale API/worker across hosts with it. Before multi-host replicas, replace the storage port with private encrypted object storage supporting server-side authorization, checksums, lifecycle/deletion, and a reviewed upload/finalize protocol; store object keys, never presigned URLs. Back up encrypted retained audio together with compatible database snapshots or document that audio is intentionally non-restorable; test cross-artifact restore consistency. User deletion must remove live objects, replicas/versions according to provider policy, reconciliation artifacts, and eventually backups under the published retention window.

Start and inspect:

```bash
docker compose --env-file .env.compose config
docker compose --env-file .env.compose build --pull
docker compose --env-file .env.compose up --wait
docker compose --env-file .env.compose ps
docker compose --env-file .env.compose logs --no-log-prefix migrate
```

`docker compose config` can render secrets from environment substitution; do not paste its output into tickets. Passing `--env-file` matters because Compose automatically loads `.env`, not an arbitrarily named `.env.compose`, for `${...}` interpolation. The service-level `env_file` separately supplies `APP_*` values inside the containers. Add every other required `APP_*` secret/config introduced by earlier phases to this ignored file. Use separate local/development data, never a production database from a laptop Compose stack.

## 7. Health endpoints and complete worksheets

These operational endpoints are not user resources. They live outside `/api/v1`, return tiny bodies, and reveal no dependency hostname, credentials, exception, or schema revision. Pydantic response models forbid unknown fields. Configure platform probes with timeouts; do not turn liveness into a cascading dependency test.

### Endpoint 1 — liveness

- **Purpose/method/path/auth:** Tell an orchestrator the process can serve HTTP; `GET /health/live`; no user auth, network exposure limited by platform where possible.
- **Path/query:** No path parameters. The baseline ignores query parameters because probes commonly append cache busters; they never affect the result. No request body is defined.
- **Success:** `200 application/json` `{ "status": "live" }`; `Cache-Control: no-store`; never include version/secrets.
- **Errors:** A dead/unresponsive process yields connection failure/timeout. Application-level unexpected failure is sanitized `500 INTERNAL_SERVER_ERROR`; it must not query PostgreSQL/provider and therefore does not return dependency errors.
- **Tables/transaction/side effects:** None, no DB session, no external calls, no log per successful probe at info level.
- **Tests:** Works with DB down, tiny latency, exact schema/status/content type/cache header, unsupported method `405`, no dependency injection that opens a DB session.

### Endpoint 2 — readiness

- **Purpose/method/path/auth:** Indicate whether this instance should receive traffic; `GET /health/ready`; no user auth but normally internal.
- **Path/query/body:** None; same explicit unknown-query policy; no body/null fields.
- **Success:** `200` `{ "status": "ready" }`, `Cache-Control: no-store` when DB responds and current Alembic heads exactly equal image-expected heads.
- **Errors:** `503 SERVICE_NOT_READY` using the common safe error envelope when DB is unreachable, pool checkout times out, or revision is incompatible; `500 INTERNAL_SERVER_ERROR` only for an unclassified bug. Do not expose SQL, host, or revision.
- **Tables/transaction:** Bounded `SELECT 1` and read `alembic_version` in a read-only/rolled-back short session; no feature rows; no commit.
- **Side effects:** Low-cardinality readiness metric/log on transitions, not noisy per-probe stack traces.
- **Tests:** Ready at head; DB down/timeouts/old or extra head -> `503`; liveness remains `200`; pool session closes; sanitized body; method/auth/network policy.

### Endpoint 3 — internal metrics

- **Purpose/method/path/auth:** Export aggregate process/application metrics; `GET /metrics`; only internal network plus platform authentication/mTLS or protected scrape identity—never normal bearer-user access.
- **Path/query/body:** No path parameters or body. Ignore query parameters in the baseline; authentication and content negotiation are header-based.
- **Success:** `200` using the installed Prometheus client’s documented text media type and optional gzip/content negotiation; `Cache-Control: no-store`.
- **Errors:** `401/403 METRICS_ACCESS_DENIED` at gateway/application boundary; `406 NOT_ACCEPTABLE` if strict negotiation; sanitized `500`. Public routing should commonly be absent rather than relying only on app auth.
- **Tables/transaction/side effects:** Normally reads in-process aggregates, no DB. A queue-depth collector may run separately with a bounded cached query, not per scrape under unbounded load.
- **Tests:** Unauthorized/internet route unavailable, expected names/types, no user/query/run IDs or secrets as labels, bounded scrape time/cardinality, correct content type.

Readiness implementation can cache expected heads loaded from files at startup:

```python
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session


def expected_alembic_heads(config_path: str = "alembic.ini") -> frozenset[str]:
    script = ScriptDirectory.from_config(Config(config_path))
    return frozenset(script.get_heads())


def database_is_ready(session: Session, expected_heads: frozenset[str]) -> bool:
    session.execute(text("SELECT 1"))
    actual = frozenset(session.execute(text("SELECT version_num FROM alembic_version")).scalars())
    return actual == expected_heads
```

Apply strict statement/pool timeouts so readiness fails quickly. During a rolling expand/contract release, define which adjacent revisions are compatible; exact heads is the beginner-safe policy when the migration job finishes before API rollout.

## 8. Configuration and secrets

Split configuration into:

- non-secret release config: environment, log level, exact CORS origins, feature flags, pool sizes, timeouts;
- secrets: database URL/password, JWT signing/encryption keys, OAuth/provider keys, webhook secrets;
- immutable build metadata: commit/image digest, application version, expected migration heads.

Pydantic settings should fail startup on missing/invalid required values. Do not silently use a development secret in production. Avoid logging the settings object; use `SecretStr` but remember it is display protection, not encryption.

Production secrets come from the platform’s secret store as mounted files or environment according to platform threat model. Rotate with overlapping key IDs where necessary. Restrict who can read deployment config and database backups. Never `COPY .env`, bake secrets into image `ARG`/layers, commit Compose passwords, or pass secrets in URLs.

## 9. Database pools, shutdown, and worker behavior

Total possible connections = API replicas × per-process pool allowance + workers + migration/admin margin. Set `pool_size`, `max_overflow`, `pool_timeout`, and database-side limits deliberately. `pool_pre_ping` helps stale connections but is not a substitute for timeouts/readiness. Add statement/network timeouts by workload.

On SIGTERM:

1. API stops accepting new traffic, allows bounded in-flight completion, then closes pools.
2. Worker stops claiming, finishes/checkpoints or safely abandons current step, releases/lets lease expire, closes clients/pools.
3. Container runtime waits `stop_grace_period`, then may kill. The durable contract recovers a killed worker.

Test shutdown during a read request, transaction before commit, external action with unknown outcome, and long job. Never acknowledge success before the durable commit.

## 10. CI pipeline

A CI system should verify the same contracts from clean state:

1. Pin runner/action dependencies (production workflows pin action commit SHAs, not only mutable major tags).
2. Install the committed hashed development lock and then the project without resolving dependencies again.
3. Run formatting check, lint, types, unit tests.
4. Start a digest/patch-pinned PostgreSQL service and run `alembic upgrade head` on blank DB.
5. Run PostgreSQL integration/contract tests and `alembic check`.
6. Build the image with digest-pinned Python input.
7. Inspect non-root user, start image, hit live/ready, send SIGTERM.
8. Scan dependencies/image and produce an SBOM where the chosen platform supports it.

Illustrative GitHub Actions skeleton (replace action tags and image with reviewed immutable SHAs/digests):

```yaml
name: ci
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17.6-bookworm
        env:
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: ai_workspace_test
        options: >-
          --health-cmd "pg_isready -U test_user -d ai_workspace_test"
          --health-interval 5s --health-timeout 3s --health-retries 12
        ports:
          - 5432:5432
    env:
      APP_ENVIRONMENT: test
      APP_DATABASE_URL: postgresql+psycopg://test_user:test_password@127.0.0.1:5432/ai_workspace_test
      APP_TEST_DATABASE_URL: postgresql+psycopg://test_user:test_password@127.0.0.1:5432/ai_workspace_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13.7"
          cache: pip
      - run: python -m pip install --require-hashes -r requirements-dev.lock
      - run: python -m pip install --no-build-isolation --no-deps -e .
      - run: ruff format --check .
      - run: ruff check .
      - run: mypy app
      - run: alembic upgrade head
      - run: alembic check
      - run: pytest
```

Keep fake-provider tests default. A scheduled/manual live smoke test uses a dedicated account, tiny budget, separate secrets, and never gates every pull request on paid/unstable providers.

## 11. Migration release discipline

Run migration once, observe it, then release compatible code. A rollback of application code is safe only if the schema remains compatible. Destructive `downgrade` is often riskier than a forward fix.

Use expand/contract for consequential changes:

1. **Expand:** add nullable column/table, compatible constraint, or index. Large indexes may need PostgreSQL `CONCURRENTLY` and Alembic’s appropriate autocommit block.
2. Deploy code that can read old/new and dual-write if needed.
3. Backfill in bounded resumable batches, with metrics and no giant lock/transaction.
4. Switch reads and verify.
5. Add validated `NOT NULL`/constraint when data complies.
6. **Contract:** remove old path/column only in a later release after rollback window.

Before release, estimate locks/runtime/disk, test on production-like volume, back up, and define abort criteria. Never assume Alembic autogenerate knows safe rollout sequencing.

## 12. Observability that answers operational questions

### Structured logs

Write JSON or stable key/value logs to stdout/stderr. Include timestamp, level, service/version/environment, request/trace ID, route template (not raw URL), status, latency, user pseudonymous internal ID only when policy permits, job/run ID, attempt, and safe error code. Redact headers, cookies, tokens, OAuth codes, prompts, email/memory bodies, SQL parameters, and provider payloads.

### Metrics

- HTTP request count/error count/latency histogram by method, route template, status class.
- DB pool in-use/wait/timeout and transaction failure.
- Queue depth/oldest age, claims, lease expiry, retries, terminal failures, dead letters.
- Provider requests/latency/rate-limit/failure by controlled provider/operation.
- Spend/usage by controlled dimensions; never user ID or prompt as a label.
- Process CPU/memory/restarts and readiness state.

Alert on user impact: sustained error/latency, readiness fleet loss, old queue age, migration failure, backup failure/age, restore-drill failure, spend anomaly, or provider circuit opening. A dashboard without an action/runbook is decoration.

### Traces

Correlate API enqueue -> database job -> worker attempt -> provider call using trace/span links, because worker work occurs later rather than as a child of the original live request. Sample thoughtfully and sanitize attributes. OpenTelemetry transports telemetry; it is not itself storage/visualization.

## 13. Backup and restore drill

Define:

- **RPO:** maximum acceptable lost data duration.
- **RTO:** maximum acceptable restoration duration.
- backup type/frequency/retention, encryption, separate account/region/location, access and deletion policy;
- logical dumps for portability and/or managed physical backups + WAL/PITR for smaller RPO;
- who receives failure alerts and who may restore.

For a small local drill, create a logical custom-format dump outside both the repository and database volume. Keep `backups/` in `.gitignore` as defense in depth, but this command deliberately uses a permission-restricted OS temporary directory and refuses a resolved path inside the workspace. It assumes the chapter's ignored `.env.compose` is shell-compatible:

```bash
set -a
. ./.env.compose
set +a
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${APP_DATABASE_URL:?APP_DATABASE_URL is required}"

AIW_WORKSPACE="$(pwd -P)"
AIW_BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ai-workspace-backup.XXXXXX")"
AIW_BACKUP_DIR="$(cd "$AIW_BACKUP_DIR" && pwd -P)"
case "$AIW_BACKUP_DIR/" in
  "$AIW_WORKSPACE/"*) echo "refusing a backup path inside the repository" >&2; exit 1 ;;
esac
chmod 700 "$AIW_BACKUP_DIR"
AIW_BACKUP_FILE="$AIW_BACKUP_DIR/ai_workspace.dump"

docker compose --env-file .env.compose exec -T db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$AIW_BACKUP_FILE"
test -s "$AIW_BACKUP_FILE"
shasum -a 256 "$AIW_BACKUP_FILE"
echo "backup retained at: $AIW_BACKUP_FILE"

AIW_RESTORE_DB=ai_workspace_restore_test
case "$AIW_RESTORE_DB" in
  *_restore_test) ;;
  *) echo "restore target lacks required _restore_test suffix" >&2; exit 1 ;;
esac
if [ "$AIW_RESTORE_DB" = "$POSTGRES_DB" ]; then
  echo "restore target equals source database" >&2
  exit 1
fi
case "$APP_DATABASE_URL" in
  */"$POSTGRES_DB") ;;
  *) echo "APP_DATABASE_URL does not end in the declared source database" >&2; exit 1 ;;
esac
AIW_RESTORE_DATABASE_URL="${APP_DATABASE_URL%/*}/${AIW_RESTORE_DB}"

# createdb fails safely if the disposable name already exists; this runbook never drops it.
docker compose --env-file .env.compose exec -T db \
  createdb -U "$POSTGRES_USER" "$AIW_RESTORE_DB"
docker compose --env-file .env.compose exec -T db \
  pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" \
  -d "$AIW_RESTORE_DB" < "$AIW_BACKUP_FILE"

docker compose --env-file .env.compose run --rm --no-deps \
  -e APP_DATABASE_URL="$AIW_RESTORE_DATABASE_URL" migrate
docker compose --env-file .env.compose run --rm --no-deps \
  -e APP_DATABASE_URL="$AIW_RESTORE_DATABASE_URL" \
  -e APP_RESTORE_EXPECTED_DATABASE="$AIW_RESTORE_DB" \
  api python -m app.ops.restore_smoke
```

`app.ops.restore_smoke` must first query `current_database()` and fail unless it exactly equals `APP_RESTORE_EXPECTED_DATABASE` and ends `_restore_test`. It then compares Alembic heads and performs only representative reads/count/invariant checks across auth, notes, durable jobs, memory, research sources/citations, and comparisons. It contains no `CREATE`, `DROP`, `DELETE`, `TRUNCATE`, or write-path smoke. The migration command and smoke command receive only the derived restore URL; neither can target the source URL. No cleanup command appears here intentionally: inspect and retain the disposable database until the drill record is complete, then use a separately reviewed, exact-target cleanup procedure.

The dump file remains outside the repository and live volume; copy it only to approved encrypted backup storage under its retention policy. A successful command is not enough: record duration, source backup timestamp, checksum, expected/actual heads, invariants, failures, and remediation. Periodically restore the production-class backup mechanism into an isolated environment. Production restores use a new database/cluster and reviewed cutover; never pipe a dump into the source database or “test” restore over the only copy.

PostgreSQL continuous archiving plus base backups enables point-in-time recovery but is version-specific and operationally more complex. Prefer the managed service’s documented mechanism when deployed there, then test it.

## 14. Deployment and recovery runbook

### Deploy

1. Review CI, migration SQL/locks/runtime, compatibility, backup freshness, incident status.
2. Build once from reviewed commit; record application image digest and SBOM; scan/sign according to platform.
3. Deploy/run the one-shot migration job and stop if it fails.
4. Roll out API/worker with the same image digest; API readiness gates traffic.
5. Run authenticated smoke tests for health, auth, one read/write, enqueue/worker completion.
6. Observe error rate, latency, pools, queue age, retries, provider failures, and spend through a defined window.
7. Record release/version/migration heads and close only after criteria pass.

### Recover or roll back

- Stop/limit traffic or disable risky features if continuing causes damage.
- Preserve logs/timelines and identify last known good app/schema/data state.
- If schema is backward compatible, roll application image back by digest.
- If data/schema is damaged, restore into a new database, validate, then switch using a reviewed cutover; do not improvise destructive commands against the only copy.
- Reconcile durable external actions with `outcome_unknown` before retrying.
- Communicate scope/status, then write an incident review and prevention tasks.

## 15. Security hardening

- Run non-root with fixed UID/GID; drop Linux capabilities and use a read-only root filesystem plus explicit writable temp paths when platform/dependencies allow.
- Expose only API/TLS router publicly. Database, metrics, and admin interfaces stay private.
- Terminate TLS with a maintained platform/proxy; configure trusted proxy headers/hosts, exact CORS origins, secure cookies, CSRF policy where cookies authenticate.
- Patch/rebuild pinned bases regularly; scan OS/Python dependencies and review provenance/licenses.
- Protect CI secrets and pull-request trust boundaries. Untrusted fork code must not receive deployment/provider credentials.
- Use least-privilege DB roles: release migrator differs from runtime where practical; runtime does not own unrelated databases.
- Rate-limit auth, job creation, uploads, research/model spending, and webhook endpoints.
- Encrypt backups/secrets and test key recovery/rotation. Do not log sensitive bodies.
- Restrict outbound network by service. The Phase 9 fetcher deserves stronger isolation than the API.
- Define dependency and security-update response, vulnerability reporting, and secret-revocation procedures.

### Production auth-throttling build slice

Phase 2's `POST /api/v1/auth/register` and `POST /api/v1/auth/login` need distributed abuse control before public production traffic. Do this at two layers: a coarse network limit at the only public TLS gateway, then an application limit using a shared store. A process-local dictionary is invalid once there are multiple API replicas and loses state on restart. For this stack, PostgreSQL is a buildable first shared store; at higher attack volume move the same port to a dedicated distributed limiter so authentication floods do not consume the primary database pool.

Add `app/modules/auth/throttle.py` with an `AuthThrottle.consume(operation, account_signal, network_signal, now)` port and a PostgreSQL implementation. Add an Alembic-managed `auth_throttle_buckets` table: `(operation,signal_kind,key_digest)` primary key, exact numeric `tokens`, aware `refilled_at`, and `expires_at`; checks enforce closed operation/kind enums and nonnegative tokens. The implementation sorts the two keys, upserts missing buckets, locks both rows `FOR UPDATE`, and refills both from an injected database/application clock. If **both** can pay, subtract one token from both; if either cannot, subtract from neither and calculate the longest required wait. Commit this tiny transaction, then return allow/deny. It runs **before** password hashing/verification and in a separate transaction so a failed login or later registration rollback still consumes capacity, while one depleted key cannot unfairly drain the other on denied requests. A periodic bounded delete removes only expired buckets.

Use versioned server configuration, initially:

| Operation/key | Burst capacity | Refill |
|---|---:|---:|
| login/account | 5 | 1 token / 180 seconds |
| login/network | 30 | 1 token / 10 seconds |
| register/account | 3 | 1 token / 1,200 seconds |
| register/network | 10 | 1 token / 360 seconds |

These are starting controls, not universal safe numbers. The gateway also enforces a coarse source rate (for example, 120 auth requests/minute) in one shared gateway policy/store across replicas. Tune from privacy-safe metrics and threat model; add step-up/CAPTCHA only as a separately designed mechanism. Do not create a permanent account lock that lets one attacker deny a victim indefinitely.

Derive `account_signal = HMAC-SHA256(throttle_pepper, normalized_email)` even when the account does not exist, so timing/status cannot enumerate users. Derive `network_signal` from the socket peer or a trusted client prefix—IPv4 `/24`, IPv6 `/56`—then HMAC it with a distinct rotating pepper. The gateway must **strip and overwrite** `Forwarded`/`X-Forwarded-For`. The app trusts those headers only when the immediate socket peer belongs to an exact configured proxy CIDR and parses the configured hop count; otherwise it ignores them. Reject malformed chains rather than guessing. Never use the raw email/IP as a key, log field, metric label, or API response.

If either application bucket denies, return `429` with the shared envelope code `AUTH_RATE_LIMITED`, a generic message, and `Retry-After: <integer-seconds>` equal to the ceiling of the longest denied-bucket wait, clamped 1–3,600. Do not reveal which key fired or emit per-account remaining-limit headers. Existing Phase 2 `401` responses remain indistinguishable for nonexistent/wrong-password accounts. If the shared limiter cannot make a definite decision, fail register/login closed with `503 AUTH_THROTTLE_UNAVAILABLE` and `Retry-After: 5`; already authenticated routes remain governed by their own availability policy. Set a short statement/pool timeout and reserve limiter capacity so an outage cannot hang every request. The gateway's independent coarse policy remains active during app/store failure.

Log only operation, allow/deny/error, policy version, rounded wait bucket, trusted-proxy decision, request ID, and short-lived pseudonymous HMAC prefixes if policy permits. Never log credentials, normalized email, raw address/header chain, bucket digest, or whether an account exists. Metrics use bounded operation/decision labels only.

Tests are part of the slice:

- unit tests with an injected clock for initial capacity, exact refill instant, one microsecond before/at boundary, longest wait, expiry, IPv4/IPv6 prefixing, HMAC rotation, and malformed proxy chains;
- PostgreSQL concurrency tests launching `capacity + 1` simultaneous consumes and proving exactly `capacity` allow, tokens never go negative, and two simulated API replicas share the same result;
- proxy integration tests for direct client, trusted one/two-hop chain, untrusted peer spoofing `X-Forwarded-For`, gateway overwrite, and IPv4-mapped IPv6;
- API contract tests for `429` body/code/integer `Retry-After`, `503` fail-closed behavior, no account/key disclosure, register and login both covered, and nonexistent/existing/wrong-password signals having the same throttle contract;
- privacy/log tests seeding recognizable email/IP/password/header values and asserting none occur in logs, metrics, responses, or bucket rows.

That heading is the Phase 11 anchor for hardening the Phase 2 auth endpoints without rewriting Phase 2's learning slice.

## 16. End-to-end build order

Keep the locally working, non-containerized application green after every step. This order makes each new failure attributable:

1. Freeze the already passing local command set: `alembic upgrade head`, `pytest`, API start, and one worker iteration with fakes.
2. Add strict settings/redaction and `/health/live`; test without Docker.
3. Add `/health/ready` with bounded DB/head checks and failure tests; add internal metrics after auth/network isolation is defined.
4. Produce reviewed hashed runtime/dev locks and record exact supported Python/PostgreSQL versions.
5. Build the non-root image; inspect UID, files, layers, import, `alembic heads`, startup, and SIGTERM.
6. Add only the Compose database and named volume; prove connectivity and persistence after container replacement.
7. Add the one-shot `migrate` service; prove blank upgrade and intentional failure prevents dependents.
8. Add the API service/readiness and then the worker using the same image; prove the closed worker registry advances every durable job kind with fakes.
9. Add structured logs, bounded metrics, trace correlation, graceful shutdown, pool budgets, and alerts with runbook links.
10. Implement the production auth-throttling slice above and trusted-proxy integration before exposing auth publicly.
11. Mirror clean migration/PostgreSQL tests/image build/health/shutdown in CI; pin actions and isolate untrusted forks from secrets.
12. Rehearse expand/contract migration and app rollback with production-like data volume.
13. Run the outside-repository dump/new-database restore/read-only smoke drill and record RPO/RTO evidence.
14. Write and rehearse deploy, rollback, provider outage, queue recovery, secret rotation, and data-restore runbooks; only then select a production platform and adapt its official controls.

Checkpoint each step in version control and its decision/runbook. Do not add provider live calls, a public port, or production secrets merely to prove the container starts.

## 17. Testing and operational exercises

### Automated checks

- Unit: health policy, redaction, settings failure, metric labels, graceful worker decision.
- PostgreSQL: blank upgrade, `alembic check`, every model constraint, job lease recovery.
- Container: clean build, UID 10001, expected files only, no env secrets/layers, live/ready behavior, SIGTERM.
- Compose: migration finishes before API/worker, data survives container replacement, database is not publicly bound.
- CI contract: same Python/PostgreSQL families and commands as supported development path.

### Exercises

1. Build on a clean machine/VM with no host Python package and prove the test/API path.
2. Replace only the API container and confirm notes/jobs persist in the named volume.
3. Stop PostgreSQL: liveness stays `200`, readiness becomes sanitized `503`, then recovers.
4. Start from a blank volume and inspect the one migration job. Scale API to two and prove migrations did not race.
5. SIGTERM a worker while it holds a job lease; restart and observe safe expiry/recovery without duplicate external action.
6. Introduce an old Alembic head and prove readiness rejects it.
7. Run a backup, restore into a fresh database, migrate, smoke-test, record RTO and discovered gaps.
8. Add one expand/contract field through all stages; document application rollback at each stage.
9. Search image history/files/log capture for a seeded fake secret and make the test fail if found.
10. Trigger provider `429`, DB pool timeout, queue-age alert, and backup-failure alert in a safe environment; follow the runbook.

Checkpoint:

- [ ] Python and PostgreSQL image inputs are exact digest-pinned reviewed references.
- [ ] Application runs non-root and responds to SIGTERM gracefully.
- [ ] Compose has a named DB volume and separate migration/API/worker commands.
- [ ] Liveness is dependency-free; readiness checks DB and compatible migration heads.
- [ ] CI verifies clean migration and real PostgreSQL tests.
- [ ] Secrets are absent from source, image layers, responses, telemetry, and fixtures.
- [ ] Logs/metrics/traces answer concrete operational questions with bounded cardinality.
- [ ] A backup exists outside live storage and a restore drill passes.
- [ ] Deploy/recovery steps and migration compatibility are written and rehearsed.

## 18. Debugging playbook

Start with the first failing boundary and preserve evidence. Avoid random rebuild/restart loops; they erase the state that explains a failure.

| Symptom | Commands/checks | What the result means |
|---|---|---|
| Image will not build or import | `docker compose --env-file .env.compose build --pull --progress=plain api`; `docker image history ai-workspace-api:dev`; `docker run --rm ai-workspace-api:dev python -c "import app; print(app.__file__)"` | Read the first build error. Check build context, lock/hash/platform, copied package/migrations, base digest, and secret-free layers. A host import succeeding proves nothing about the image. |
| Compose interpolation/topology is wrong | `docker compose --env-file .env.compose config --quiet`; `docker compose --env-file .env.compose ps -a`; `docker compose --env-file .env.compose images` | A config failure is host interpolation; a stopped service is runtime. Do not paste fully rendered config because it can contain secrets. Confirm API/worker use one image ID and DB has no public port. |
| Migration blocks startup | `docker compose --env-file .env.compose logs --no-log-prefix migrate`; `docker compose --env-file .env.compose exec -T db psql -U ai_workspace -d ai_workspace -c 'TABLE alembic_version'`; `docker compose --env-file .env.compose run --rm --no-deps migrate alembic heads` | Compare database rows with image heads. Inspect the first SQL/lock/permission error; do not mark the service complete or run API against a half-migrated schema. |
| Live works but ready is `503` | `curl -i http://127.0.0.1:8000/health/live`; `curl -i http://127.0.0.1:8000/health/ready`; `docker compose --env-file .env.compose logs --since=5m api`; `docker compose --env-file .env.compose exec -T db pg_isready -U ai_workspace -d ai_workspace` | Liveness proves only the process. Check DB DNS/credentials/pool/statement timeout and migration-head compatibility using request ID; response must stay sanitized. |
| Job remains queued/running | `docker compose --env-file .env.compose ps worker`; `docker compose --env-file .env.compose logs --since=10m worker`; query the exact job's status, handler kind, `next_attempt_at`, lease owner/expiry, attempt, and safe error via `psql` | Confirm handler registration, clock/time zone, lease fencing, budget and provider fake. Do not manually change status; repair/restart and let documented lease recovery act. |
| CI differs from laptop | Inspect the first failing CI step; run the exact lock/install/lint/type/Alembic/pytest command locally; compare `python --version`, PostgreSQL version, image digest, environment names, and migration heads | “Works locally” usually means uncommitted file, unpinned dependency/tool, different DB behavior, hidden environment, or test order. Never add retries until reproducibility is understood. |
| Backup/restore smoke fails | `test -s "$AIW_BACKUP_FILE"`; `shasum -a 256 "$AIW_BACKUP_FILE"`; `docker compose --env-file .env.compose exec -T db psql -U "$POSTGRES_USER" -d ai_workspace_restore_test -c 'SELECT current_database(), count(*) FROM alembic_version'`; rerun only the `app.ops.restore_smoke` command with the restore URL | First distinguish empty/corrupt dump, restore error, migration mismatch, and invariant failure. Preserve the dump/disposable DB and logs. Never redirect `pg_restore` to the source or drop/recreate the source to make a drill pass. |

For any incident, record UTC time, release/image digest, environment, request/job ID, first error and stack-trace cause chain, migration heads, safe configuration names, commands/results, and hypothesis. Logs answer “what happened”; the stack trace should be read from the final exception backward through `raise ... from ...`; persisted rows answer “what committed.” Redact before sharing and turn a resolved production-only failure into the smallest safe automated regression test.

## 19. What you should be able to explain after this phase

Explain image versus container; tag versus digest; why non-root and exec-form matter; named volume versus backup; stateless API versus durable worker; why migrations run once; liveness versus readiness; connection-pool capacity; graceful shutdown and lease recovery; immutable build config versus runtime secret; structured logs versus metrics versus traces; cardinality; expand/contract migration; RPO versus RTO; logical dump versus PITR; and why a successful server start is not production readiness.

## 20. Current official primary documentation

- [Docker build best practices](https://docs.docker.com/build/building/best-practices/) — trusted/minimal bases, digest pinning, `.dockerignore`, non-root `USER`.
- [Docker Compose startup order and health conditions](https://docs.docker.com/compose/how-tos/startup-order/)
- [Docker Compose volumes](https://docs.docker.com/engine/storage/volumes/)
- [FastAPI in containers](https://fastapi.tiangolo.com/deployment/docker/) — exec-form command, image/process/replication guidance.
- [`pip-compile` reference](https://pip-tools.readthedocs.io/en/latest/reference/pip-compile/) — hashed locks, extras, and `--all-build-deps` for wheel/editable build requirements.
- [Kubernetes liveness, readiness, and startup probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) — useful semantics even when deploying elsewhere.
- [SQLAlchemy connection pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)
- [Alembic cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) and [operation reference](https://alembic.sqlalchemy.org/en/latest/ops.html)
- [PostgreSQL backup and restore](https://www.postgresql.org/docs/current/backup.html), [SQL dumps](https://www.postgresql.org/docs/current/backup-dump.html), and [continuous archiving/PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)
- [GitHub Actions PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers) — adapt if GitHub is your CI.
- [OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/) and [metrics](https://opentelemetry.io/docs/concepts/signals/metrics/)
- [OWASP Docker Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html)
- [Redis Lua scripting atomicity](https://redis.io/docs/latest/develop/programmability/eval-intro/) — relevant if the `AuthThrottle` port later moves from PostgreSQL to a dedicated shared Redis implementation; scripts must be fixed/parameterized and bounded.

Read the official documentation for your actual deployment/managed PostgreSQL/secret/backup platform before launch. Its health routing, shutdown, filesystem, proxy, backup, PITR, encryption, and recovery guarantees—not this generic chapter—define production behavior.
