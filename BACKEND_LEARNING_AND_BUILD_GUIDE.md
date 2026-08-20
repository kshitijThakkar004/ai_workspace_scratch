# Personal AI Workspace Backend: Learning and Build Guide

This is a build manual, not a generated project. You will type, run, break, inspect, and repair the code yourself. The snippets are deliberately small reference pieces. Do not paste the entire guide into files in one sitting.

The seven-day foundation sprint ends with a working Notes API backed by PostgreSQL. The complete curriculum then builds the larger AI workspace through Phases 2–11. Trying to implement authentication, Gmail, agents, memory, research, and voice in that same first sprint would hide the backend concepts you want to learn.

This file is the complete foundation handbook for Phases 0–1. Continue through every later build chapter using the [complete curriculum index](COMPLETE_BACKEND_CURRICULUM.md); the shorter future-phase summaries in Section 7 remain the architectural preview.

# 1. High-level architecture

## 1.1 Start with a modular monolith

Build one FastAPI application and one PostgreSQL database. Organize the code into clear feature modules, but deploy it as one unit. This is a **modular monolith**.

```mermaid
flowchart LR
    C["Frontend, CLI, or voice client"] --> H["FastAPI HTTP boundary"]
    H --> R["Feature router"]
    R --> S["Application service"]
    S --> P["Repository"]
    P --> DB[("PostgreSQL")]

    S --> A["Provider adapter"]
    A --> X["Gmail, Calendar, Slack, Telegram, model APIs"]

    W["Future scheduler or worker"] --> S
    M["Alembic migrations"] --> DB
    T["pytest"] --> H
    T --> S
    T --> P
```

The request path in Phase 1 is:

```text
HTTP request
  -> FastAPI parses the path/query/body
  -> Pydantic validates the request
  -> router translates HTTP into a use-case call
  -> service enforces rules and owns the transaction
  -> repository issues SQLAlchemy operations
  -> PostgreSQL protects persistent data
  -> response model controls the JSON returned
```

Why not microservices?

- You are one developer and the features share users, jobs, logs, and transactions.
- A monolith lets you learn HTTP, SQL, transactions, testing, and integrations without also learning distributed deployment and cross-service failure.
- Clear module boundaries can later be extracted if real scale or team ownership demands it. Starting distributed does not make weak boundaries strong.

## 1.2 Technology decisions

| Concern | Initial choice | Why |
|---|---|---|
| Language | Python 3.12 or newer | Modern typing syntax and broad library support. Pick one installed version and record it. |
| Web framework | FastAPI | Type-driven validation, OpenAPI generation, dependency injection, and a small HTTP layer. |
| ASGI server | Uvicorn | Runs the FastAPI ASGI application and accepts network connections. FastAPI is not itself the network server. |
| Database | PostgreSQL | Production-grade relational behavior, constraints, transactions, indexing, JSON support, and a future path to full-text/vector search. |
| ORM | SQLAlchemy 2.x | It exposes mappings, queries, sessions, flushing, and transactions clearly. This is more educational than combining API and persistence models. |
| API schemas | Pydantic 2.x | Validates untrusted boundary data and controls serialization/OpenAPI schemas. |
| Migrations | Alembic | Creates an ordered, reviewable history of database changes. |
| PostgreSQL driver | Psycopg 3 | The DBAPI driver used by SQLAlchemy to communicate with PostgreSQL. |
| Tests | pytest + FastAPI `TestClient` | Plain assertions, fixtures, and readable API tests. |
| Concurrency in Phase 1 | Synchronous SQLAlchemy with ordinary `def` routes | Keeps session and transaction behavior visible. A blocking database driver must not be called directly inside `async def`. Learn async later when a measured need exists. |
| Deployment | Local process first; Docker later | First understand the process and database on the host. Containerization should package a system you already understand. |

### Why SQLAlchemy rather than SQLModel initially?

SQLModel is useful, but it deliberately makes SQLAlchemy and Pydantic feel like one model system. Your learning goal benefits from seeing two separate boundaries:

- A SQLAlchemy model answers: **what is stored in a table?**
- A Pydantic schema answers: **what may enter or leave this API operation?**

Those shapes often diverge. A database row may contain `password_hash`, encrypted OAuth tokens, internal flags, or audit fields that must never appear in a response.

### Why synchronous code first?

`async` is a concurrency mechanism, not a badge of quality. Use `async def` when the libraries you call support `await`. Phase 1 uses synchronous Psycopg/SQLAlchemy, so normal `def` routes are the coherent choice. FastAPI runs those route functions in a thread pool. A later phase can compare sync and async implementations using load measurements.

## 1.3 Responsibilities of each layer

| Layer | It owns | It must not own |
|---|---|---|
| Router | Method, path, HTTP parameters, response model, status, headers, dependency wiring | SQL queries, commits, provider SDK calls, or complex business rules |
| Service | One use case, business invariants, orchestration, domain exceptions, transaction decision | FastAPI `Request`, `Response`, or `HTTPException` |
| Repository | SQLAlchemy queries and persistence operations for one feature | HTTP status codes, JSON response shapes, or cross-feature workflows |
| ORM model | Table/column/relationship mapping | Request validation or public response policy |
| Pydantic schema | API input/output validation and serialization | Database sessions or persistence behavior |
| Exception handler | Domain/validation exception to public HTTP error | Business decisions |
| Adapter | Translation to a specific outside provider | Decisions about the overall use case |

A healthy Phase 1 route is boring: validate inputs, call one service method, set an HTTP-specific header if necessary, and return the result.

A **fat controller** is a route that starts checking ownership, querying SQLAlchemy, committing, sending messages, catching every exception, and building dictionaries. It is hard to test because HTTP, business rules, database state, and side effects are tangled together.

## 1.4 Architectural rules to keep

- PostgreSQL is the source of truth. Do not replace it with SQLite in database integration tests; SQLite differs in types, constraints, concurrency, and SQL behavior.
- One SQLAlchemy `Session` is created for one request and always closed.
- A service/use case owns `commit` or `rollback`. A repository queries or stages changes and may `flush` when a use case needs generated SQL results, but it does not commit independently.
- Never use `Base.metadata.create_all()` as a substitute for migrations.
- ORM entities are internal. Explicit response models whitelist public fields.
- External providers sit behind small interfaces so tests can use fakes.
- Add a worker only when reminders, research, or retryable calls need work beyond a request lifetime.
- Defer Redis, Celery, a vector database, microservices, generic CRUD base classes, and agent frameworks until a real requirement earns them.
- Store timestamps in UTC and serialize them as ISO 8601 values with a timezone.
- Logs contain identifiers and events, not passwords, tokens, full note bodies, email bodies, or raw model prompts by default.

## 1.5 How to use this guide

For each small step:

1. Read the concept and predict what the code will do.
2. Type the snippet rather than copying it blindly.
3. Run it immediately.
4. Change one thing and observe the result.
5. Write a test.
6. Explain the behavior in your own words in `docs/learning-journal.md`.
7. Commit only after the checkpoint works.

When an exercise intentionally asks you to fill in a variant or a future-phase candidate omits wiring, use the error, editor, and official documentation to investigate. Phase 0/1 reference snippets should still be internally complete; if one does not run after the stated setup, treat that as something to diagnose and correct—not as permission to guess silently. When genuinely stuck, reduce the problem to the smallest failing example before asking an AI for a finished implementation.

---

# 2. Phased roadmap

Build Phases 0 and 1 from this foundation handbook. Each later row is a **future phase relative to the foundation** and has its own complete, linked handbook in the curriculum index.

| Phase | Build | Main engineering lesson | Exit artifact |
|---|---|---|---|
| 0 | Walking skeleton and `/health` | Process, HTTP, JSON, typing, validation, tests, logs | Running FastAPI process with a green test |
| 1 | Notes CRUD + PostgreSQL | Contracts, SQL, ORM, migrations, layers, transactions, integration tests | Working and tested Notes API |
| Future 2 | Users, login, ownership | Authentication versus authorization | User-scoped resources |
| Future 3 | Tasks, reminders, scheduling foundation | State machines, time zones, due queries, idempotency | Tested task/reminder CRUD and due-work foundation |
| Future 4 | Conversations and model gateway | Provider abstraction, streaming, timeouts, usage logging | Chat API with a fake and then real model |
| Future 5 | Tool registry and execution logs | Allowlisting, validation, approval, auditability | Safe tool execution boundary |
| Future 6 | Gmail and Google Calendar | OAuth, scopes, token lifecycle, provider adapters, idempotent writes | Read-only first, then controlled writes |
| Future 7 | Telegram, email, Slack notifications and voice | Delivery retries, webhook security, transcription, confirmation | Multi-channel commands/notifications |
| Future 8 | Persistent memory | Provenance, retention, retrieval evaluation | User-controlled memory records |
| Future 9 | Deep research jobs | Background workflows, citations, resumability | Asynchronous sourced research |
| Future 10 | Model comparison and evaluation | Reproducibility, fair measurement, partial failure | Comparable, measured model runs |
| Future 11 | Docker and production hardening | Observability, deployment, migrations, recovery | Deployable and recoverable system |

Do not advance merely because an endpoint returns `200`. Advance when you can explain the phase, reproduce it on a fresh database, and make its tests pass.

---

# 3. Suggested folder structure

This is the intended shape after Phase 1, not a command to create every empty folder on day one.

```text
personal-ai-workspace-api/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── docs/
│   ├── endpoint-template.md
│   ├── learning-journal.md
│   └── decisions/
│       ├── 001-modular-monolith.md
│       └── 002-sync-sqlalchemy-first.md
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── router.py
│   │   ├── error_schemas.py
│   │   ├── exception_handlers.py
│   │   └── request_context.py
│   ├── core/
│   │   ├── config.py
│   │   └── logging.py
│   ├── db/
│   │   ├── base.py
│   │   └── session.py
│   └── modules/
│       └── notes/
│           ├── model.py
│           ├── schemas.py
│           ├── repository.py
│           ├── service.py
│           ├── dependencies.py
│           └── router.py
└── tests/
    ├── unit/
    │   └── notes/
    │       ├── test_schemas.py
    │       └── test_service.py
    └── integration/
        ├── conftest.py
        ├── api/
        │   ├── test_health.py
        │   └── test_notes.py
        └── repositories/
            └── test_notes_repository.py
```

Why each part exists:

- `pyproject.toml` describes the Python package, supported Python version, dependency bounds, and test configuration. It is a repeatable declaration within those bounds, not a bit-for-bit lock file or a virtual environment.
- `README.md` is the operational entry point: setup, run, migrate, and test commands.
- `.env.example` lists required configuration names with fake/local placeholders. The real `.env` is ignored.
- `alembic/` stores executable schema history. ORM models show the desired present; migrations show how an old database reaches it.
- `docs/endpoint-template.md` is the mandatory design gate for every new endpoint.
- `docs/learning-journal.md` records predictions, failures, explanations, and links in your own words.
- `docs/decisions/` stores short architecture decision records: context, decision, alternatives, and consequences.
- `app/main.py` is the **composition root**. It creates the FastAPI application and registers shared plumbing and routers. It does not contain Notes rules.
- `app/api/` holds shared HTTP mechanics: the version router, typed error metadata, request context, and exception translation.
- `app/core/` holds truly application-wide configuration and logging. Do not turn it into an unowned `utils.py` drawer.
- `app/db/` answers “how does this process talk to the database?” It defines the declarative base, engine, session factory, and request session dependency.
- `app/modules/notes/` is one cohesive feature. Later `auth`, `tasks`, `chat`, and `memory` become sibling modules.
- `model.py` maps Python objects to database rows.
- `schemas.py` defines external request/response contracts.
- `repository.py` contains SQLAlchemy reads and writes.
- `service.py` implements use cases and transaction decisions.
- `dependencies.py` constructs a feature service from a request-scoped session without adding a separate dependency-injection framework.
- `router.py` is the Notes HTTP boundary.
- `tests/unit/` runs rules without HTTP or PostgreSQL.
- `tests/integration/` proves boundaries with the real FastAPI app and a dedicated PostgreSQL test database.

Feature-first organization keeps related code close. A global `routers/`, `services/`, and `repositories/` layout becomes a collection of unrelated files as the application grows.

Create an `__init__.py` in each Python package directory (`api`, `core`, `db`, `modules`, `notes`, and so on). Some were omitted from the tree only to keep it readable.

---

# 4. The endpoint contract gate

**Rule: do not implement an endpoint until this worksheet is complete.** Put one copy in your design notes for every endpoint. If you cannot specify a field or error yet, the endpoint is not ready to code.

```markdown
## Endpoint: <human name>

1. Purpose: <one caller-centered sentence>
2. HTTP method: <GET | POST | PATCH | PUT | DELETE>
3. Path: </api/v1/...>
4. Authentication/authorization: <who can do what to which resource?>
5. Path parameters: <name, type, meaning>
6. Query parameters: <name, type, default, min/max, meaning>
7. Request body:
   - required fields:
   - optional fields:
   - nullable fields:
   - forbidden/ignored fields:
8. Successful response:
   - status:
   - headers:
   - JSON body:
9. Errors:
   - condition -> status -> stable error code
10. Database tables:
    - reads:
    - writes:
11. Transaction boundary: <what must succeed or fail as one unit?>
12. External side effects: <provider calls, retry and idempotency behavior>
13. Test cases:
    - happy path
    - invalid/missing/extra input
    - boundary values
    - missing resource
    - conflict
    - unauthorized/forbidden
    - database/provider failure
    - response and database side effects
```

The user-requested nine items are all present; the extra authentication, transaction, side-effect, and idempotency questions prevent expensive omissions later.

## 4.1 Endpoint naming rules

- Use plural resource nouns: `/api/v1/notes`, not `/getNotes` or `/create-note`.
- Use lowercase kebab-case for multiword resources: `/research-jobs`.
- Use path parameters for identity: `/notes/{note_id}`.
- Use query parameters for filtering, sorting, and pagination: `/notes?limit=20&offset=0`.
- Nest only for real containment: `/conversations/{conversation_id}/messages` is reasonable; `/users/{user_id}/notes/{note_id}/comments/...` quickly becomes brittle.
- Use verbs only for commands that do not fit CRUD: `POST /reminders/{id}/snooze` or `POST /drafts/{id}/send`.
- Choose one trailing-slash policy. This guide uses **no trailing slash**.
- Version domain APIs under `/api/v1`. Keep process endpoints such as `/health` outside that version.

## 4.2 Method rules

| Method | Meaning | Safe? | Idempotent? | Typical use here |
|---|---|---:|---:|---|
| `GET` | Read a representation | Yes | Yes | Get/list notes |
| `POST` | Create or start a command | No | Usually no | Create note, start research job |
| `PUT` | Completely replace a resource at a known URI | No | Yes | Rare; do not use as a synonym for PATCH |
| `PATCH` | Change only supplied fields | No | Depends on operation | Edit a note title/content |
| `DELETE` | Remove the addressed resource | No | Intended to be idempotent semantically, though response policy varies | Delete a note |

“Safe” means the client did not ask to change server state. “Idempotent” means repeating the same request has the same intended effect as making it once. These properties matter to caches, retries, and clients.

## 4.3 Status-code rules

| Code | Use |
|---:|---|
| `200 OK` | Successful read or update with a response body |
| `201 Created` | Resource created; return it and preferably a `Location` header |
| `202 Accepted` | Asynchronous job accepted but not finished |
| `204 No Content` | Success with no response body, commonly delete |
| `400 Bad Request` | Request is syntactically valid but violates a general request rule not better represented elsewhere; use sparingly |
| `401 Unauthorized` | No valid authentication credentials; usually include `WWW-Authenticate` |
| `403 Forbidden` | Authenticated, but not permitted |
| `404 Not Found` | Addressed resource is absent or deliberately hidden from this caller |
| `409 Conflict` | Current state or a known uniqueness/version rule conflicts with the request |
| `422 Unprocessable Content` | FastAPI/Pydantic could parse the request but validation failed |
| `429 Too Many Requests` | Rate limit exceeded |
| `500 Internal Server Error` | Unexpected bug/failure; do not intentionally expose its details |
| `503 Service Unavailable` | Temporarily unable to serve because a required dependency is unavailable |

Do not return `200` for every outcome with `{ "success": false }`. HTTP already has a result channel: the status code.

## 4.4 Body-design rules

- Create separate `Create`, `Update`, and `Read` schemas. They have different permissions and optionality.
- Treat “field omitted” and “field explicitly set to `null`” as different states in PATCH.
- Reject unexpected write fields with Pydantic `extra="forbid"`; silently ignoring a typo is unfriendly and can become a mass-assignment risk.
- Return one resource directly. Return a list inside an envelope so pagination metadata can be added without breaking the shape.
- Use stable field names and types. Do not expose internal database column names accidentally.
- Use UUID strings for identifiers and timezone-aware ISO 8601 strings for timestamps.
- Never include secrets, password hashes, provider tokens, SQL, or stack traces.
- A response model is both documentation and an output safety filter.

## 4.5 Consistent error contract

Use one public shape:

```json
{
  "error": {
    "code": "NOTE_NOT_FOUND",
    "message": "Note was not found",
    "details": {},
    "request_id": "0ac9e756-9ee2-4afb-a802-3a6dd0abf2d2"
  }
}
```

- `code` is stable and machine-readable.
- `message` is safe for a person and may evolve carefully.
- `details` contains safe structured context such as validation locations.
- `request_id` connects the client failure to server logs.

Domain code raises `NoteNotFound`; the shared HTTP layer turns it into `404 NOTE_NOT_FOUND`. The service must not raise FastAPI `HTTPException`, because then a non-HTTP caller such as a scheduler would depend on the web framework.

---

# 5. Phase 0 — Foundations and walking skeleton

## Phase 0 outcome

At the end of Phase 0:

- the Python environment can be recreated within declared major-version bounds;
- the FastAPI process starts;
- `GET /health`, `/docs`, and `/openapi.json` work;
- a test calls `/health`;
- configuration and logs have clear homes;
- you can trace a request and read a Python traceback.

PostgreSQL is not required for the first walking skeleton. `/health` proves that the API process can answer; it does not pretend to prove database readiness.

**Prerequisites:** a terminal, editor, Git, and Python 3.12+; no prior backend or database knowledge is assumed. **Non-goals:** PostgreSQL, authentication, CRUD, background jobs, external providers, Docker, and production readiness. Phase 0 proves only the local HTTP/process/test loop.

## 5.1 Beginner concepts first

### What is a backend?

A backend is a long-running process that accepts requests, applies rules, reads or changes state, communicates with other systems, and returns responses. The frontend is one client of that contract. A CLI, mobile app, scheduler, Telegram bot, or test can use the same backend.

### Process, host, and port

Running Uvicorn starts an operating-system process. It listens on a network address such as `127.0.0.1` (this machine) and a port such as `8000` (one numbered doorway). `http://127.0.0.1:8000/health` identifies the protocol, host, port, and path.

### Request and response

An HTTP request contains a method, target path, headers, and sometimes a body. A response contains a status code, headers, and sometimes a body.

```text
GET /health HTTP/1.1
Host: 127.0.0.1:8000

HTTP/1.1 200 OK
content-type: application/json

{"status":"ok"}
```

JSON is a text data format, not a Python dictionary. FastAPI parses JSON into Python values and serializes Python/Pydantic values back to JSON.

### Framework inversion of control

You define a route function but do not call it yourself. The decorator registers it. Uvicorn receives a request, FastAPI chooses the matching route, validates inputs, and calls your function. This is **inversion of control**: the framework controls when your code runs.

### Type hints do real framework work

In ordinary Python, `name: str` is mostly information for humans and tools. FastAPI and Pydantic inspect type hints at runtime to validate input, generate JSON Schema, and build OpenAPI documentation.

### ASGI, Uvicorn, FastAPI, and Pydantic

- **ASGI** is a standard interface between an async-capable Python server and application.
- **Uvicorn** is the server implementation accepting network traffic.
- **FastAPI** routes requests, resolves dependencies, and builds OpenAPI.
- **Pydantic** validates and serializes typed data.

Do not describe all four as “the API server”; know which job each does.

## 5.2 Check your tools

Run these yourself:

```bash
python3 --version
git --version
```

Use Python 3.12 or newer. If `python3` points to the chosen interpreter, create the project and virtual environment:

```bash
mkdir personal-ai-workspace-api
cd personal-ai-workspace-api
git init
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

A virtual environment is an isolated Python installation plus packages for this project. It prevents one project’s dependency versions from changing another project. `.venv` contains installed executables; `.env` will later contain configuration text. They are unrelated.

Verify that activation worked:

```bash
which python
python --version
python -m pip --version
```

The paths should include the project’s `.venv` directory.

## 5.3 Plan the Phase 0 files and create them incrementally

This is the complete Phase 0 shape. Start with `app/main.py` and the health test, then add `core/config.py` and `core/logging.py` when Sections 5.7 and 5.8 introduce them. Creating a directory early is harmless; writing unexplained code into it is not.

```text
personal-ai-workspace-api/
├── app/
│   ├── __init__.py
│   ├── main.py
│   └── core/
│       ├── __init__.py
│       ├── config.py
│       └── logging.py
├── docs/
│   ├── endpoint-template.md
│   └── learning-journal.md
├── tests/
│   └── test_health.py
├── .env.example
├── .gitignore
├── README.md
└── pyproject.toml
```

`__init__.py` marks `app` as an importable Python package. A module is one `.py` file; a package is an importable directory of modules.

Start `pyproject.toml` with only Phase 0 needs:

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "personal-ai-workspace-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.30,<1",
    "pydantic-settings>=2.6,<3",
]

[project.optional-dependencies]
dev = [
    "httpx>=0.27,<1",
    "python-dotenv>=1,<2",
    "pytest>=8,<10",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

The version ranges communicate compatible major versions without pretending that a 2026 guide knows the exact patch version you will install later. This is repeatable within bounds, not bit-for-bit reproducible. After the first green install, save `python -m pip freeze` output as a diagnostic artifact; introduce a real lock workflow before deployment.

Install the project in editable mode with its development extras:

```bash
python -m pip install -e ".[dev]"
```

“Editable” means imports refer to your working source, so editing `app/` does not require reinstalling. `dev` adds testing tools that the running production application does not need.

Use at least this `.gitignore`:

```gitignore
.venv/
.env
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
htmlcov/
.DS_Store
```

Never commit a real secret. `.env.example` may initially contain:

```dotenv
APP_ENVIRONMENT=development
APP_LOG_LEVEL=INFO
```

## 5.4 Design `/health` before coding

### Endpoint contract: liveness health check

- **Purpose:** Prove that the API process can receive and answer HTTP requests.
- **Method:** `GET` because this is a read with no requested side effect.
- **Path:** `/health`; it describes the process rather than versioned domain data.
- **Authentication/authorization:** Public; it exposes no user or dependency data.
- **Path/query parameters:** None.
- **Request body:** None.
- **Response body:** `{ "status": "ok" }`.
- **Success headers:** `Content-Type: application/json`; Phase 0 has no request-correlation header yet, and Phase 1 adds `X-Request-ID` to this response through shared middleware.
- **Possible errors:** Unsupported method -> `405 HTTP_405`; unexpected server failure -> `500 INTERNAL_SERVER_ERROR`. Both use the shared envelope once Phase 1 installs the HTTP/exception handlers. This endpoint does not touch PostgreSQL.
- **Status codes:** `200`, `405`, `500`.
- **Tables touched:** None.
- **Transaction/external side effects:** No Session, transaction, or external call.
- **Tests:** exact `200`; exact body; JSON content type; no request body required; `POST /health` is `405`.

Later, `/ready` will verify required dependencies and return `503` when the application cannot do real work. Liveness answers “is the process alive?” Readiness answers “can it serve its responsibilities?”

## 5.5 Build the smallest app

Type this into `app/main.py`:

```python
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_app() -> FastAPI:
    application = FastAPI(
        title="Personal AI Workspace API",
        version="0.1.0",
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return application


app = create_app()
```

Why each decision exists:

- `HealthResponse` makes the output contract explicit and appears in OpenAPI.
- `Literal["ok"]` says the only valid successful value is exactly `"ok"`, not any string.
- An application factory gives tests and future configuration one composition point.
- The decorator registers method + path + metadata with FastAPI.
- `response_model` validates/filters the returned representation.
- The normal `def` function is compatible with the synchronous style used in Phase 1. FastAPI runs sync route functions in a thread pool.
- `app` is the module-level ASGI object Uvicorn imports.

The string `app.main:app` later means: import package `app`, module `main`, then find variable `app`.

Run it:

```bash
uvicorn app.main:app --reload
```

`--reload` watches source files and restarts the development process. Do not use it in production.

Inspect all three:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

Then call it without a browser:

```bash
curl -i http://127.0.0.1:8000/health
```

`-i` shows response headers and status as well as the body. Use the interactive documentation to execute the same operation and find its generated response schema in `openapi.json`.

## 5.6 Write the first test

Type `tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_reports_process_is_alive() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"].startswith("application/json")


def test_health_rejects_unsupported_method() -> None:
    response = client.post("/health")

    assert response.status_code == 405
```

Run:

```bash
python -m pytest
```

This follows **Arrange, Act, Assert**:

- Arrange: construct a client for the application.
- Act: send `GET /health`.
- Assert: verify observable contract behavior.

Why `python -m pytest`? It unambiguously uses the pytest installed for the currently selected Python interpreter.

Now deliberately change the expected body to make the test fail. Read pytest’s comparison. Restore it and rerun. A test you have never seen fail has taught you less than it could.

## 5.7 Add configuration deliberately

Create `app/core/config.py` only when you introduce configuration:

```python
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Underlying ideas:

- Environment variables keep deployment-specific values outside source code.
- `APP_` prevents collisions with unrelated process variables.
- Real environment variables take precedence over `.env`; `.env` is a local convenience.
- Pydantic parses and validates declared fields. With `extra="ignore"`, a misspelled variable such as `APP_LOG_LEVL` is ignored and the default can still be used; it does **not** become a valid field, but this configuration also does not detect the typo. Check critical production configuration explicitly and consider stricter loading when the deployment environment is controlled.
- Caching constructs settings once per process. Tests that change the environment after first access must call `get_settings.cache_clear()`.

Do not print a complete settings object once it contains a database password or OAuth secret.

## 5.8 Add basic logging

Create `app/core/logging.py`:

```python
import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
```

Call it while composing the app. A log is evidence about an event, not a substitute for a debugger and not a data dump. Prefer:

```python
logger.info("note_created note_id=%s", note_id)
```

over:

```python
logger.info("created note %s with token %s", whole_note, access_token)
```

The `%s` form lets logging defer string interpolation when the message will not be emitted.

## 5.9 How to read a traceback

Create a temporary practice route that raises `RuntimeError("traceback practice")`. Call it once.

Use this order:

1. Reproduce the exact request once.
2. Read the final line: exception type and message.
3. Move upward to the first frame whose path belongs to your project.
4. Read that file, function, line, and the values that reached it.
5. State one hypothesis before editing.
6. Make the smallest change that tests the hypothesis.
7. Add or improve a regression test.

Framework frames show how execution travelled; the first project frame usually shows where your code handed the framework a bad value. Do not “fix” the first scary line in a third-party package.

Common beginner categories:

- `ModuleNotFoundError`: wrong interpreter, environment, package install, working directory, or import path.
- `TypeError`: called something with the wrong Python shape.
- Pydantic validation response (`422`): the request does not match its schema; this is normally a client-contract outcome, not a server crash.
- `500`: unhandled server failure; inspect logs and traceback.
- Connection refused: no process is listening at the host/port, or PostgreSQL is not running.

## 5.10 Phase 0 exercises

Do these without adding production features:

1. Call `/health` through a browser, Swagger UI, and `curl -i`. Write down what each displays differently.
2. Add a temporary `GET /practice/greet?name=...` endpoint with `name: str`. Observe OpenAPI.
3. Change the parameter to `times: int` and send `times=banana`. Explain the `422` response.
4. Add `response_model=HealthResponse` but return an invalid status value. Observe whether this is a caller error or server error.
5. Intentionally misspell `app.main:app` in the Uvicorn command and interpret the import failure.
6. Set `APP_LOG_LEVEL=DEBUG` for one process invocation and explain environment-variable precedence.
7. Draw the request lifecycle from socket to route and response without looking at this guide.
8. Write three sentences explaining why `.venv`, `.env`, and `pyproject.toml` are different.

## 5.11 Phase 0 checkpoint

Do not continue until all are true:

- [ ] A fresh terminal can activate the environment and start the documented command.
- [ ] `/health`, `/docs`, and `/openapi.json` work.
- [ ] `python -m pytest` passes.
- [ ] The health contract exists before its code in your notes.
- [ ] `.env` and `.venv` are ignored by Git.
- [ ] No real secret is tracked.
- [ ] You can identify the exception type, message, and first project frame in a traceback.
- [ ] `README.md` contains setup, run, and test commands you actually executed.

## What you should be able to explain after Phase 0

- What FastAPI, Uvicorn, ASGI, Pydantic, and pytest each do.
- How method + path select a route function.
- Why a decorator registers a function you do not call yourself.
- How type hints affect validation and OpenAPI.
- The difference between Python values and JSON text.
- Why virtual environments exist.
- Why liveness and readiness are different.
- How to approach a traceback without random edits.

---

# 6. Phase 1 — Notes CRUD with PostgreSQL

## Phase 1 outcome

At the end of Phase 1 you will have:

- a PostgreSQL `notes` table created entirely through Alembic;
- create, list, retrieve, partial-update, and delete endpoints;
- separate router, service, repository, ORM, and Pydantic responsibilities;
- one consistent error shape, including validation errors;
- service unit tests and API/repository integration tests using a dedicated PostgreSQL test database;
- a project that can be rebuilt from a blank database and still pass its tests.

The API deliberately has no authentication yet. Phase 2 adds users and makes every Notes query owner-scoped.

**Prerequisites:** the Phase 0 checkpoint is complete and you can run its test from a fresh terminal. **Non-goals:** users/authorization, async SQLAlchemy, generic CRUD abstractions, jobs, provider calls, Docker, and production deployment. This phase deliberately implements one feature with synchronous SQLAlchemy and real local/test PostgreSQL.

## 6.1 Learn the relational model before the ORM

A relational table has named columns and rows. PostgreSQL—not Pydantic—is the final authority for persistent constraints, because imports, scripts, migrations, or future services may write without using the HTTP API.

Key concepts:

- **Primary key:** stable unique row identity.
- **Constraint:** a rule PostgreSQL refuses to violate.
- **Transaction:** changes that become visible/durable together on commit or are discarded on rollback.
- **Index:** a data structure that can make specified lookup/order patterns faster at the cost of storage and write work.
- **SQL:** the language ultimately executed. An ORM produces SQL; it does not eliminate the need to understand it.

Before mapping Notes, practice in `psql` with a temporary table:

```sql
BEGIN;

CREATE TEMP TABLE learning_notes (
    id integer PRIMARY KEY,
    title text NOT NULL
);

INSERT INTO learning_notes (id, title)
VALUES (1, 'first');

SELECT id, title
FROM learning_notes
WHERE id = 1;

UPDATE learning_notes
SET title = 'changed'
WHERE id = 1;

DELETE FROM learning_notes
WHERE id = 1;

ROLLBACK;
```

Predict the result after each statement. `ROLLBACK` discards the transaction, and a temporary table disappears with its database session. This is safe practice, not your application migration.

## 6.2 Install and prepare PostgreSQL

Use the [official PostgreSQL downloads](https://www.postgresql.org/download/) for your platform. Confirm:

```bash
psql --version
```

Connect as a local PostgreSQL administrator and create a non-superuser application role plus two databases:

```sql
CREATE ROLE ai_workspace_app
    LOGIN
    PASSWORD 'replace-with-a-local-only-password';

CREATE DATABASE ai_workspace_dev
    OWNER ai_workspace_app;

CREATE DATABASE ai_workspace_test
    OWNER ai_workspace_app;
```

Development data is useful while you work. Tests must be free to create and delete data. A `_test` suffix gives the test harness a safety condition before cleanup. Do not run the application as PostgreSQL’s superuser; least privilege limits the blast radius of mistakes.

Add Phase 1 runtime dependencies to `pyproject.toml`:

```toml
dependencies = [
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.30,<1",
    "pydantic-settings>=2.6,<3",
    "sqlalchemy>=2.0,<3",
    "alembic>=1.13,<2",
    "psycopg[binary]>=3.2,<4",
]
```

Then reinstall:

```bash
python -m pip install -e ".[dev]"
```

Create a real untracked `.env`:

```dotenv
APP_ENVIRONMENT=development
APP_LOG_LEVEL=INFO
APP_DATABASE_URL=postgresql+psycopg://ai_workspace_app:replace-with-a-local-only-password@localhost:5432/ai_workspace_dev
APP_TEST_DATABASE_URL=postgresql+psycopg://ai_workspace_app:replace-with-a-local-only-password@localhost:5432/ai_workspace_test
```

Show every required name—but no working credential—in Phase 1 `.env.example`:

```dotenv
APP_ENVIRONMENT=development
APP_LOG_LEVEL=INFO
APP_DATABASE_URL=postgresql+psycopg://CHANGE_ME_USER:CHANGE_ME_PASSWORD@localhost:5432/CHANGE_ME_DATABASE
APP_TEST_DATABASE_URL=postgresql+psycopg://CHANGE_ME_USER:CHANGE_ME_PASSWORD@localhost:5432/CHANGE_ME_DATABASE_test
```

If a password contains URL-significant characters such as `@`, `/`, or `%`, URL-encode it or learn SQLAlchemy’s `URL.create()` rather than guessing.

Verify the application role directly:

```bash
psql -h localhost -p 5432 -U ai_workspace_app -d ai_workspace_dev -W
```

Enter the same local-only password when prompted. This avoids putting it in shell history or the process argument list. Inside `psql`, run `SELECT current_user, current_database();` and exit with `\q`.

## 6.3 Finalize configuration

Extend `app/core/config.py`:

```python
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str
    test_database_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

The application only uses `database_url`. The test harness reads `test_database_url`, verifies it, then makes it the application database before importing the Engine.

## 6.4 Understand Engine, connection, Session, and transaction

These are not synonyms:

- **Engine:** process-wide entry point that knows the SQL dialect, driver, and connection pool.
- **Connection pool:** reuses a bounded set of database connections.
- **Connection:** one checked-out conversation with PostgreSQL.
- **Session:** SQLAlchemy’s unit of work. It tracks ORM objects, issues queries, flushes changes, and participates in a transaction.
- **Flush:** sends pending SQL inside the current transaction; it does not make the transaction durable.
- **Commit:** successfully ends the transaction and makes its changes permanent/visible according to isolation rules.
- **Rollback:** discards uncommitted changes and resets failed transaction state.
- **Refresh:** reloads an ORM object, including database-generated values.

Create `app/db/base.py`:

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`Base.metadata` is SQLAlchemy’s in-memory description of imported mapped tables. Alembic compares it with the actual database.

Create `app/db/session.py`:

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionFactory = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
```

Create one Engine per database per process, not per request. The Engine opens connections lazily. One request gets one Session through a generator dependency, and the `with` block guarantees close. Closing releases resources; it does not commit. `autoflush=False` makes flush timing less surprising while learning. `expire_on_commit=False` keeps loaded attributes readable; refresh still deliberately loads server-generated values.

Never store one global Session. A Session is mutable transaction state and is not safe for concurrent requests.

## 6.5 Design the table

| Column | PostgreSQL intent | Rule |
|---|---|---|
| `id` | UUID primary key | Application-generated; identity is not authorization |
| `title` | `varchar(200)` | Non-null and non-blank after trimming |
| `content` | `text` | Nullable; `NULL` means no/cleared content |
| `created_at` | timezone-aware timestamp | Non-null, database default now |
| `updated_at` | timezone-aware timestamp | Non-null, database default now; ORM-generated updates use the database clock |

Create `app/modules/notes/model.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint(
            "title ~ '[^[:space:]]'",
            name="ck_notes_title_not_blank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
```

Pydantic gives callers friendly early errors. PostgreSQL constraints protect the table if another writer bypasses the API. The 50,000-character content cap is currently an API resource limit, not a table invariant; add a database check if every writer must obey it. SQLAlchemy’s mapped `onupdate=func.now()` adds a database-clock expression to ORM-generated updates. If all raw SQL writers must maintain `updated_at` later, add a tested database trigger through a migration; SQLAlchemy `onupdate` is not a PostgreSQL trigger.

## 6.6 Create and inspect the migration

Initialize once:

```bash
alembic init alembic
```

Do not put the database password in `alembic.ini`. Modify generated `alembic/env.py` so it sees settings, Engine, metadata, and the mapped class:

```python
from alembic import context

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine
from app.modules.notes import model as notes_model  # registers Note

target_metadata = Base.metadata
```

Keep the generated offline/online structure, configured like this:

```python
def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
```

The otherwise-unused model import registers its table in `Base.metadata`.

Generate a candidate:

```bash
alembic revision --autogenerate -m "create notes table"
```

Stop and read it. Verify that `upgrade()` creates only `notes`; types, nullability, primary key, check, and timestamp defaults match; `downgrade()` undoes only this revision; and nothing unrelated is dropped. Autogenerate drafts—it does not certify.

Then apply and inspect:

```bash
alembic upgrade head
alembic current
alembic history
```

Use `\d+ notes` in `psql`. Write one valid and one invalid `INSERT` and read the named constraint failure. Practice `alembic downgrade -1` then `upgrade head` only against a disposable database. A model edit does not alter an existing database until a migration is applied.

## 6.7 Endpoint contracts for the complete Notes API

A Note response is:

```json
{
  "id": "ea3dd684-d5a5-4013-8171-b1bcf8a82b38",
  "title": "Learn transactions",
  "content": "A commit makes the write durable.",
  "created_at": "2026-08-12T10:15:30.123456+00:00",
  "updated_at": "2026-08-12T10:15:30.123456+00:00"
}
```

A list response is:

```json
{
  "items": [],
  "pagination": {"limit": 20, "offset": 0, "total": 0}
}
```

All Notes endpoints are public only because authentication is deferred. Common protocol outcomes include `405` for unsupported methods and a sanitized `500` for unexpected failures.

### Endpoint 1: create a note

- **Purpose:** Create and persist a note from a title and optional content.
- **Method/path:** `POST /api/v1/notes`.
- **Auth/parameters:** Public only in this phase; no path or query parameters.
- **Request body:** `title` required, trimmed, 1–200; `content` optional string or `null`, maximum 50,000; unknown/server-owned fields forbidden.
- **Response:** `201`, full Note, and an absolute `Location` URL pointing to `GET /api/v1/notes/{id}`. This matches the `request.url_for(...)` implementation below.
- **Headers:** JSON content type, `Location`, and `X-Request-ID`.
- **Errors:** Invalid input -> `422 REQUEST_VALIDATION_FAILED`; unexpected failure -> safe `500 INTERNAL_SERVER_ERROR`.
- **Database tables touched/SQL:** `notes`; one `INSERT`, followed by the explicit post-commit `refresh()` `SELECT`.
- **Transaction:** Insert and generated values are one write transaction.
- **External side effects:** None.
- **Tests:** Minimal body; content; explicit null; trimming; missing/blank/long title; long content; unknown field; request ID header; generated UUID/timestamps; Location; persistent row.

### Endpoint 2: list notes

- **Purpose:** Return a deterministic page.
- **Method/path:** `GET /api/v1/notes?limit=20&offset=0`.
- **Auth/path parameters:** Public only in this phase; no path parameters.
- **Request body:** None.
- **Query:** `limit` default 20, range 1–100; `offset` default 0, minimum 0.
- **Response:** `200 {items, pagination}`.
- **Headers:** JSON content type and `X-Request-ID`.
- **Errors:** Invalid query -> `422 REQUEST_VALIDATION_FAILED`; unexpected failure -> safe `500 INTERNAL_SERVER_ERROR`.
- **Database tables touched:** `notes`; ordered page `SELECT` plus count `SELECT`.
- **Transaction/external side effects:** Read-only request transaction/session; no commit and no external call.
- **Ordering:** `created_at DESC, id DESC`; ID is a deterministic tie-breaker.
- **Tests:** Empty/multiple rows; exact order/envelope; defaults/custom page; total; 0/101 limit; negative offset; wrong type.

Offset pagination is fine for small learning data. Later, cursor pagination handles large offsets and shifting pages. The page and count queries can also observe slightly different concurrent states under normal `READ COMMITTED` isolation; that accepted tradeoff should be understood.

### Endpoint 3: retrieve one note

- **Purpose:** Retrieve the Note addressed by UUID.
- **Method/path:** `GET /api/v1/notes/{note_id}`.
- **Auth/parameters:** Public only in this phase; UUID `note_id` path; no query parameters.
- **Request body:** None.
- **Response body/status:** `200` with the full Note.
- **Headers:** JSON content type and `X-Request-ID`.
- **Errors:** Malformed UUID -> `422 REQUEST_VALIDATION_FAILED`; absent valid UUID -> `404 NOTE_NOT_FOUND`; unexpected -> safe `500 INTERNAL_SERVER_ERROR`.
- **Database tables touched:** `notes`; one primary-key `SELECT`.
- **Transaction/external side effects:** Read-only request transaction/session; no commit and no external call.
- **Tests:** Existing, missing, malformed ID, exact public fields/types.

### Endpoint 4: partially update one note

- **Purpose:** Change supplied mutable fields and preserve omitted fields.
- **Method/path:** `PATCH /api/v1/notes/{note_id}`.
- **Auth/parameters:** Public only in this phase; UUID `note_id` path; no query parameters.
- **Request body:** At least one of `title`/`content`; null title invalid; null content explicitly clears; unknown fields forbidden.
- **Response:** `200` full updated Note.
- **Headers:** JSON content type and `X-Request-ID`.
- **Errors:** Malformed ID, empty object, invalid/null title, size, or unknown field -> `422 REQUEST_VALIDATION_FAILED`; absent row -> `404 NOTE_NOT_FOUND`; unexpected -> safe `500 INTERNAL_SERVER_ERROR`.
- **Database tables touched/SQL:** `notes`; one lookup `SELECT`, one `UPDATE`, then the explicit post-commit `refresh()` `SELECT`.
- **Transaction:** Values and `updated_at` commit together.
- **External side effects:** None.
- **Tests:** Title-only/content-only/both; omitted preserved; explicit null clears content; `{}`; null/blank title; unknown; absent/malformed ID; updated time advances and created time is unchanged.

`{}` means “no change” and is rejected. `{"content": null}` means “clear content.” Omitting `content` means “preserve it.”

### Endpoint 5: delete one note

- **Purpose:** Remove an addressed Note.
- **Method/path:** `DELETE /api/v1/notes/{note_id}`.
- **Auth/parameters:** Public only in this phase; UUID `note_id` path; no query parameters.
- **Request body:** None.
- **Response body/status:** `204` with no response body.
- **Headers:** `X-Request-ID`; no content type/body.
- **Errors:** Malformed UUID -> `422 REQUEST_VALIDATION_FAILED`; absent row -> `404 NOTE_NOT_FOUND`; unexpected -> safe `500 INTERNAL_SERVER_ERROR`.
- **Database tables touched:** `notes`; one `SELECT`, then one `DELETE`.
- **Transaction:** Delete commits as one write.
- **External side effects:** None.
- **Tests:** Existing returns bodyless 204; later GET 404; second delete 404 by chosen policy; malformed UUID 422.

Do not return `{"message":"deleted"}` with `204`. Choose a body-bearing status if you want JSON.

## 6.8 Define request and response schemas

Create `app/modules/notes/schemas.py`:

```python
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


NoteTitle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
    ),
]
NoteContent = Annotated[str, Field(max_length=50_000)]


class WriteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteCreate(WriteSchema):
    title: NoteTitle
    content: NoteContent | None = None


class NoteUpdate(WriteSchema):
    title: NoteTitle | None = None
    content: NoteContent | None = None

    @model_validator(mode="after")
    def validate_patch_semantics(self) -> "NoteUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")

        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title may not be null")

        return self


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    content: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PaginationMeta(BaseModel):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class NoteListResponse(BaseModel):
    items: list[NoteRead]
    pagination: PaginationMeta
```

Why separate models:

- Create permits only caller-writable creation fields.
- PATCH needs presence/null semantics that creation does not.
- Read includes server-owned fields but excludes future internal fields.
- `from_attributes=True` lets Pydantic read ORM attributes; it does not make the ORM entity public.
- `extra="forbid"` makes `{"titel":"typo"}` fail rather than silently losing intent.
- `model_fields_set` records what the caller actually supplied.

Write schema tests first:

```python
import pytest
from pydantic import ValidationError

from app.modules.notes.schemas import NoteUpdate


def test_patch_distinguishes_omitted_content_from_null() -> None:
    omitted = NoteUpdate(title="New title")
    cleared = NoteUpdate(content=None)

    assert omitted.model_dump(exclude_unset=True) == {"title": "New title"}
    assert cleared.model_dump(exclude_unset=True) == {"content": None}


def test_patch_rejects_empty_body() -> None:
    with pytest.raises(ValidationError):
        NoteUpdate()
```

Add title trimming, blank/maximum boundaries, null title, content size, and unknown-field tests.

## 6.9 Implement the repository

Create `app/modules/notes/repository.py`:

```python
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.notes.model import Note


class NoteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, note: Note) -> None:
        self.session.add(note)

    def get(self, note_id: UUID) -> Note | None:
        return self.session.get(Note, note_id)

    def list_page(self, *, limit: int, offset: int) -> list[Note]:
        statement = (
            select(Note)
            .order_by(Note.created_at.desc(), Note.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement).all())

    def count(self) -> int:
        statement = select(func.count()).select_from(Note)
        return self.session.scalar(statement) or 0

    def delete(self, note: Note) -> None:
        self.session.delete(note)
```

The repository stages writes but does not commit. A future use case might change a Note and enqueue a notification together. If each repository commits independently, the application can permanently save half the promise.

Write approximate SQL beside every method in your journal:

```sql
SELECT * FROM notes WHERE id = :note_id;

SELECT * FROM notes
ORDER BY created_at DESC, id DESC
LIMIT :limit OFFSET :offset;

SELECT count(*) FROM notes;
```

SQLAlchemy parameterizes values rather than building SQL with string interpolation. This is crucial for correctness and SQL-injection resistance.

## 6.10 Implement service rules and transaction ownership

Create `app/modules/notes/service.py`:

```python
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.notes.model import Note
from app.modules.notes.repository import NoteRepository
from app.modules.notes.schemas import NoteCreate, NoteUpdate


class NoteNotFoundError(Exception):
    def __init__(self, note_id: UUID) -> None:
        self.note_id = note_id
        super().__init__(f"Note {note_id} was not found")


class NoteService:
    def __init__(
        self,
        repository: NoteRepository,
        session: Session,
    ) -> None:
        self.repository = repository
        self.session = session

    def create(self, data: NoteCreate) -> Note:
        note = Note(title=data.title, content=data.content)
        self.repository.add(note)
        self._commit()
        self.session.refresh(note)
        return note

    def list_page(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Note], int]:
        notes = self.repository.list_page(limit=limit, offset=offset)
        return notes, self.repository.count()

    def get(self, note_id: UUID) -> Note:
        return self._get_or_raise(note_id)

    def update(self, note_id: UUID, data: NoteUpdate) -> Note:
        note = self._get_or_raise(note_id)
        changes = data.model_dump(exclude_unset=True)

        for field, value in changes.items():
            setattr(note, field, value)

        self._commit()
        self.session.refresh(note)
        return note

    def delete(self, note_id: UUID) -> None:
        note = self._get_or_raise(note_id)
        self.repository.delete(note)
        self._commit()

    def _get_or_raise(self, note_id: UUID) -> Note:
        note = self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def _commit(self) -> None:
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
```

The service owns missing-resource behavior, correct PATCH application, and one commit/rollback decision. The mapped `onupdate=func.now()` makes ORM-generated updates use PostgreSQL’s clock; it is **not** a database trigger and therefore does not protect a raw SQL writer. If multiple kinds of writers appear later, promote this to a true database-wide invariant. The service must not import FastAPI `Request`, `Response`, or `HTTPException`. The repository owns no status codes.

The service currently accepts Pydantic DTOs as a reasonable beginner compromise. If rules later become independent of HTTP and complex, introduce application command objects.

The explicit `refresh()` loads database-generated timestamps and intentionally costs one post-commit `SELECT`. It happens after a successful commit; if that read fails, the write is already committed. Non-idempotent create operations eventually benefit from idempotency keys because a caller receiving no response cannot safely assume nothing happened.

## 6.11 Wire dependencies without a DI framework

Create `app/modules/notes/dependencies.py`:

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.modules.notes.repository import NoteRepository
from app.modules.notes.service import NoteService


SessionDep = Annotated[Session, Depends(get_session)]


def get_note_service(session: SessionDep) -> NoteService:
    repository = NoteRepository(session)
    return NoteService(repository, session)


NoteServiceDep = Annotated[NoteService, Depends(get_note_service)]
```

Dependency injection means the route declares what it requires. FastAPI resolves the request Session, repository, and service. Tests can replace a dependency at the boundary. You do not need another container framework.

## 6.12 Add consistent errors and request IDs

Create `app/api/error_schemas.py`:

```python
from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
```

Register handlers in `app/api/exception_handlers.py`:

```python
import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.modules.notes.service import NoteNotFoundError


logger = logging.getLogger(__name__)


def request_id_for(request: Request) -> str:
    return getattr(request.state, "request_id", "unavailable")


def route_template_for(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) and template.startswith("/") else "<unmatched>"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NoteNotFoundError)
    async def note_not_found(
        request: Request,
        exc: NoteNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOTE_NOT_FOUND",
                    "message": "Note was not found",
                    "details": {"note_id": str(exc.note_id)},
                    "request_id": request_id_for(request),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_failed(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        all_errors = exc.errors()
        violations = [
            {
                "location": [str(part)[:100] for part in error["loc"][:10]],
                "message": str(error["msg"])[:300],
                "type": str(error["type"])[:100],
            }
            for error in all_errors[:20]
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "REQUEST_VALIDATION_FAILED",
                    "message": "One or more request values were invalid",
                    "details": {
                        "violations": violations,
                        "truncated": len(all_errors) > len(violations),
                    },
                    "request_id": request_id_for(request),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        try:
            phrase = HTTPStatus(exc.status_code).phrase
        except ValueError:
            phrase = "HTTP error"
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": f"HTTP_{exc.status_code}",
                    "message": phrase,
                    "details": {},
                    "request_id": request_id_for(request),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.error(
            "unhandled_request_error method=%s route=%s request_id=%s",
            request.method,
            route_template_for(request),
            request_id_for(request),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": request_id_for(request)},
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "The server could not complete the request",
                    "details": {},
                    "request_id": request_id_for(request),
                }
            },
        )
```

Domain errors become HTTP only at the boundary. Clients get one predictable shape. The real exception and traceback stay in logs; SQL and secrets never go in the `500` response.

Create `app/api/request_context.py`. Make middleware registration an explicit function so the final application factory cannot accidentally leave it as an unwired fragment:

```python
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.exception_handlers import route_template_for


logger = logging.getLogger(__name__)


def register_request_context(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = (perf_counter() - started) * 1000
            logger.info(
                "request_finished method=%s route=%s status=%s "
                "duration_ms=%.2f request_id=%s",
                request.method,
                route_template_for(request),
                status_code,
                duration_ms,
                request_id,
            )
```

The middleware is async because `call_next` is awaitable; it can coexist with sync database routes. The `finally` block records duration even when an unexpected exception unwinds through the middleware. In that exceptional path there is no response here to modify, so the catch-all handler adds the same `X-Request-ID` header itself. Log the matched route template—not `request.url.path` or the query—so IDs, future secret webhook path components, and attacker-controlled high-cardinality values do not enter ordinary logs. Before routing succeeds the helper emits the fixed `<unmatched>` label. Do not log bodies by default.

## 6.13 Keep routers thin

Create `app/modules/notes/router.py`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.api.error_schemas import ErrorResponse
from app.modules.notes.dependencies import NoteServiceDep
from app.modules.notes.schemas import (
    NoteCreate,
    NoteListResponse,
    NoteRead,
    NoteUpdate,
    PaginationMeta,
)


router = APIRouter(prefix="/notes", tags=["notes"])

COMMON_ERROR_RESPONSES = {
    422: {
        "model": ErrorResponse,
        "description": "Request validation failed",
    },
    500: {
        "model": ErrorResponse,
        "description": "Unexpected server failure",
    },
}
RESOURCE_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    404: {
        "model": ErrorResponse,
        "description": "Note was not found",
    },
}

@router.post(
    "",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
    responses=COMMON_ERROR_RESPONSES,
)
def create_note(
    payload: NoteCreate,
    request: Request,
    response: Response,
    service: NoteServiceDep,
) -> NoteRead:
    note = service.create(payload)
    response.headers["Location"] = str(
        request.url_for("get_note", note_id=note.id)
    )
    return NoteRead.model_validate(note)


@router.get(
    "",
    response_model=NoteListResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def list_notes(
    service: NoteServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NoteListResponse:
    notes, total = service.list_page(limit=limit, offset=offset)
    return NoteListResponse(
        items=[NoteRead.model_validate(note) for note in notes],
        pagination=PaginationMeta(limit=limit, offset=offset, total=total),
    )


@router.get(
    "/{note_id}",
    name="get_note",
    response_model=NoteRead,
    responses=RESOURCE_ERROR_RESPONSES,
)
def get_note(note_id: UUID, service: NoteServiceDep) -> NoteRead:
    return NoteRead.model_validate(service.get(note_id))


@router.patch(
    "/{note_id}",
    response_model=NoteRead,
    responses=RESOURCE_ERROR_RESPONSES,
)
def update_note(
    note_id: UUID,
    payload: NoteUpdate,
    service: NoteServiceDep,
) -> NoteRead:
    return NoteRead.model_validate(service.update(note_id, payload))


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=RESOURCE_ERROR_RESPONSES,
)
def delete_note(note_id: UUID, service: NoteServiceDep) -> Response:
    service.delete(note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Create `app/api/router.py`:

```python
from fastapi import APIRouter

from app.modules.notes.router import router as notes_router


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(notes_router)
```

Compose in `app/main.py`:

```python
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.api.exception_handlers import register_exception_handlers
from app.api.request_context import register_request_context
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(
        title="Personal AI Workspace API",
        version="0.1.0",
    )
    register_request_context(application)
    register_exception_handlers(application)
    application.include_router(api_router)

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return application


app = create_app()
```

The concrete `responses=...` mappings in the Notes router make OpenAPI advertise the same custom error envelope that handlers and tests enforce. Response metadata documents behavior; it does not make a handler run, so keep both pieces.

For every route, ask: does it import `select`, an ORM model, `commit`, or a provider SDK? Move that work down. A healthy route mostly declares HTTP, makes one service call, and serializes.

## 6.14 Build vertical slices, not horizontal layers

Do not write every repository, then every service, then every router without running anything. Use this sequence:

1. Finish the table model and migration.
2. Write create contract tests; implement create through schema -> repository -> service -> route.
3. Call create and inspect the row in `psql`.
4. Write retrieve tests and finish retrieve through every layer.
5. Repeat for list.
6. Repeat for PATCH, focusing on omitted versus null.
7. Repeat for delete.
8. Refactor duplicated mechanics only after a repeated pattern is real.

This keeps the system working after each slice and exposes wiring mistakes early.

## 6.15 Test with real, disposable PostgreSQL

Use three levels:

- **Schema tests:** pure Pydantic behavior, fast and exhaustive.
- **Service unit tests:** fake/mock repository and Session for rules and transaction decisions.
- **Integration tests:** real PostgreSQL for SQL, constraints, migrations, wiring, status, JSON, headers, and persistent effects.

Do not silently switch integration tests to SQLite.

For the first harness, let the application create normal request-scoped sessions and explicitly clean the one Phase 1 table around each test. This is slower than a savepoint harness but easy to understand and avoids sharing one non-thread-safe Session across `TestClient` and a sync route’s thread pool.

At the start of Phase 1, move the Phase 0 health test from `tests/test_health.py` to `tests/integration/api/test_health.py`; after composition imports database wiring, every test that imports `app.main` must live below the environment-switching integration configuration. Keep root `tests/conftest.py` empty (or limited to genuinely database-free fixtures). Put the following in `tests/integration/conftest.py`, so schema and service unit tests neither require nor mutate PostgreSQL. Pytest loads this file before collecting its descendant test modules, so it switches to the test database before any of them import `app.main` or `app.db.session`:

```python
import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.engine import make_url


# Pytest is commonly run from the repository root. Load the untracked local file
# explicitly because this guard must execute before importing application settings.
load_dotenv(dotenv_path=".env", override=False)
test_database_url = os.environ.get("APP_TEST_DATABASE_URL")
if not test_database_url:
    raise RuntimeError("APP_TEST_DATABASE_URL must be configured")

database_name = make_url(test_database_url).database
if database_name is None or not database_name.endswith("_test"):
    raise RuntimeError(
        "Refusing to run tests unless the database name ends with _test"
    )

os.environ["APP_ENVIRONMENT"] = "test"
os.environ["APP_DATABASE_URL"] = test_database_url

# These imports must happen after the environment switch.
from app.db.session import SessionFactory
from app.main import app
from app.modules.notes.model import Note


@pytest.fixture(scope="session")
def migrated_test_database() -> Iterator[None]:
    command.upgrade(Config("alembic.ini"), "head")
    yield


def delete_all_notes() -> None:
    with SessionFactory.begin() as session:
        session.execute(delete(Note))


@pytest.fixture
def clean_database(migrated_test_database: None) -> Iterator[None]:
    delete_all_notes()
    yield
    delete_all_notes()


@pytest.fixture
def client(clean_database: None) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
```

`python-dotenv` is declared in the development extra because this test harness imports it directly. `load_dotenv(..., override=False)` preserves an explicitly exported CI value and uses `.env` only as the local fallback. Keep the `_test` name check; convenience must not weaken the destructive-test guard.

Important:

- The suffix is a safety guard, not proof. Test credentials must be unable to access production.
- Pytest loads `conftest.py` before test modules; test modules must not import the app through another path first.
- This cleanup assumes tests are not concurrent against the same database. Do not add `pytest-xdist` until isolation is redesigned.
- As tables arrive, use foreign-key-aware cleanup or learn a transaction/savepoint strategy and understand its thread/session implications.
- Migrations, not `create_all`, create the test schema.
- Migration and cleanup fixtures are requested only by database tests; they are not `autouse`. Pure unit tests do not load this integration configuration at all.
- `TestClient` normally re-raises server exceptions, which is useful while debugging. A dedicated client with `raise_server_exceptions=False` lets the contract test below inspect the sanitized `500` response instead.

Representative API tests:

```python
from datetime import datetime
from uuid import UUID, uuid4


def test_create_note_returns_resource_and_location(client) -> None:
    response = client.post(
        "/api/v1/notes",
        json={
            "title": "  Learn transactions  ",
            "content": "Commit and rollback",
        },
    )

    assert response.status_code == 201
    body = response.json()
    UUID(body["id"])
    assert body["title"] == "Learn transactions"
    assert body["content"] == "Commit and rollback"
    assert datetime.fromisoformat(body["created_at"]).utcoffset() is not None
    assert datetime.fromisoformat(body["updated_at"]).utcoffset() is not None
    assert response.headers["location"].endswith(
        f"/api/v1/notes/{body['id']}"
    )


def test_missing_note_uses_consistent_error(client) -> None:
    response = client.get(f"/api/v1/notes/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOTE_NOT_FOUND"
    request_id = response.json()["error"]["request_id"]
    UUID(request_id)
    assert request_id != "unavailable"
    assert response.headers["x-request-id"] == request_id


def test_patch_preserves_omitted_field_and_can_clear_content(client) -> None:
    created = client.post(
        "/api/v1/notes",
        json={"title": "Original", "content": "Keep initially"},
    ).json()

    renamed = client.patch(
        f"/api/v1/notes/{created['id']}",
        json={"title": "Changed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["content"] == "Keep initially"

    cleared = client.patch(
        f"/api/v1/notes/{created['id']}",
        json={"content": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["content"] is None


def test_delete_has_no_body_and_note_is_gone(client) -> None:
    note = client.post(
        "/api/v1/notes",
        json={"title": "Temporary"},
    ).json()

    deleted = client.delete(f"/api/v1/notes/{note['id']}")

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/api/v1/notes/{note['id']}").status_code == 404


def test_request_logs_use_templates_not_raw_path_values(client, caplog) -> None:
    missing_id = uuid4()
    unmatched_secret = f"webhook-secret-{uuid4()}"

    with caplog.at_level("INFO"):
        client.get(f"/api/v1/notes/{missing_id}")
        client.get(f"/{unmatched_secret}")

    assert "route=/api/v1/notes/{note_id}" in caplog.text
    assert "route=<unmatched>" in caplog.text
    assert str(missing_id) not in caplog.text
    assert unmatched_secret not in caplog.text
```

Test the unexpected-error contract with a test-only route or failing dependency registered on a freshly created app; do not ship a crash route:

```python
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app


def test_unexpected_error_is_sanitized() -> None:
    failing_app = create_app()

    @failing_app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(
        failing_app,
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/_test/boom")

    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "INTERNAL_SERVER_ERROR"
    assert "secret internal detail" not in response.text
    UUID(body["request_id"])
    assert response.headers["x-request-id"] == body["request_id"]
```

Write one test for every case on every endpoint card, especially list ordering/bounds, malformed UUID, `{}` PATCH, `title: null`, size limits, and unknown fields.

A small service unit test proves layer behavior without HTTP:

```python
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.modules.notes.repository import NoteRepository
from app.modules.notes.service import NoteNotFoundError, NoteService


def test_get_missing_note_raises_domain_error_without_committing() -> None:
    repository = create_autospec(NoteRepository, instance=True)
    session = create_autospec(Session, instance=True)
    repository.get.return_value = None
    service = NoteService(repository, session)

    with pytest.raises(NoteNotFoundError):
        service.get(uuid4())

    session.commit.assert_not_called()
```

Do not heavily mock SQLAlchemy query construction. A real PostgreSQL repository test is more truthful for query behavior.

Add one worked repository test in `tests/integration/repositories/test_notes_repository.py`. It uses the migrated, cleaned database from the integration fixture:

```python
from datetime import datetime, timedelta, timezone

from app.db.session import SessionFactory
from app.modules.notes.model import Note
from app.modules.notes.repository import NoteRepository


def test_repository_lists_newest_note_first(
    clean_database: None,
) -> None:
    with SessionFactory() as session:
        repository = NoteRepository(session)
        now = datetime.now(timezone.utc)
        older = Note(title="Older", created_at=now - timedelta(minutes=1))
        newer = Note(title="Newer", created_at=now)
        repository.add(older)
        session.flush()
        repository.add(newer)
        session.commit()

        page = repository.list_page(limit=10, offset=0)

        assert [note.title for note in page] == ["Newer", "Older"]
        assert repository.count() == 2
```

`flush()` proves staged ORM objects become SQL before commit; `commit()` makes this setup visible to subsequent queries. The explicit ordering assertion tests repository SQL rather than a mocked call sequence. The integration cleanup fixture removes both rows afterward.

### Test-design rules

- Name a test after observable behavior, not a method implementation.
- Use Arrange -> Act -> Assert.
- Make each test independent and deterministic.
- Assert status, response JSON, important headers, and the persistent effect.
- Do not assert an exact random UUID or current timestamp. Assert type, presence, ordering, or invariant.
- Unit-test branching rules; integration-test boundaries and SQL.
- A regression test should fail before the fix and pass after it.
- High line coverage is not proof of useful behavioral coverage.

## 6.16 Run and manually exercise Phase 1

Start:

```bash
uvicorn app.main:app --reload
```

Create and list:

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/notes \
  -H 'Content-Type: application/json' \
  -d '{"title":"First note","content":"Built manually"}'

curl -i 'http://127.0.0.1:8000/api/v1/notes?limit=20&offset=0'
```

Use the returned UUID:

```bash
curl -i http://127.0.0.1:8000/api/v1/notes/NOTE_UUID

curl -i -X PATCH http://127.0.0.1:8000/api/v1/notes/NOTE_UUID \
  -H 'Content-Type: application/json' \
  -d '{"title":"Updated title"}'

curl -i -X DELETE http://127.0.0.1:8000/api/v1/notes/NOTE_UUID
```

Swagger UI is a second client, not your only test. Restart Uvicorn and retrieve a previously created Note to prove PostgreSQL—not process memory—holds it.

## 6.17 Debug like a backend engineer

Use this loop:

1. Reproduce the smallest failing request.
2. Record method, path, relevant headers/body, status, and response.
3. Classify the boundary: routing, validation, service rule, SQL/migration, connection, or serialization.
4. For `422`, inspect `details.violations`; there may be no server bug.
5. For `500`, read the final exception, then the first project frame.
6. Verify which configuration/database/process you are using without printing secrets.
7. State one hypothesis.
8. Create a failing regression test.
9. Make one change and run the narrowest test.
10. Run the full suite.

Useful commands:

```bash
python -m pytest -x -vv
python -m pytest tests/integration/api/test_notes.py -vv
alembic current
alembic history
```

Temporarily enable SQL evidence locally:

```python
import logging

logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
```

Turn it off afterward; bound parameters may contain sensitive values.

| Symptom | Investigate |
|---|---|
| `ModuleNotFoundError` | Interpreter, editable install, working directory, package/import path |
| Connection refused | Is PostgreSQL listening at the configured host/port? |
| Password authentication failed | Role/password/database and URL encoding; do not print full URL |
| `UndefinedTable: notes` | Migration absent or applied to a different database |
| `IntegrityError` | Named constraint, API/DB rule mismatch; rollback before Session reuse |
| `DetachedInstanceError` | ORM object used outside Session lifecycle or expired |
| `422` malformed UUID/body | Contract mismatch; inspect violation location/type |
| PATCH wipes a field | Missing `exclude_unset=True` or conflated omitted/null |
| Test rows leak | Cleanup fixture, wrong database, or concurrent test use |

### Deliberate debugging lab

Break the development database port, call one Notes endpoint, and:

1. identify the exception class;
2. find the first project frame;
3. confirm host/port without logging the password;
4. restore configuration;
5. write the diagnosis in your learning journal;
6. later add `/ready` rather than making liveness query PostgreSQL.

## 6.18 Phase 1 exercises

1. Implement create, retrieve, list, patch, delete in that order. Complete your worksheet before comparing it with the reference.
2. Handwrite approximate SQL, enable SQL logs, and compare.
3. Remove `exclude_unset=True` temporarily; demonstrate the PATCH data-loss bug, then restore it.
4. Move `commit()` into the repository temporarily in a disposable edit. Explain how “update task + create notification” can become half committed. Restore service ownership.
5. Return an ORM object without a response model in a practice route. List contract/security risks.
6. Generate a second migration adding nullable `archived_at`; inspect upgrade/downgrade. Do not add archive behavior without its contract.
7. Add `q` title search only after defining case sensitivity, substring/prefix behavior, whitespace, ordering, escaping, limits, table/index, and tests.
8. Explain why Note titles should not be unique.
9. Describe a lost update when two clients PATCH the same row. Research optimistic versioning, but defer it.
10. Force an exception between two staged writes and prove rollback leaves neither.
11. Recreate a blank disposable database using only `alembic upgrade head`.
12. Restart the API and retrieve existing data.
13. Compare a Pydantic validation failure with a PostgreSQL constraint failure.
14. Test malformed JSON, malformed UUID, unknown field, empty PATCH, null title, and missing row; explain each status.

## 6.19 Phase 1 checkpoint

- [ ] All five endpoint worksheets exist and match behavior.
- [ ] `alembic current` is at head in development and test databases.
- [ ] A blank database reaches the complete schema using migrations alone.
- [ ] No startup code calls `create_all()`.
- [ ] All five endpoints work through `curl` and `/docs`.
- [ ] A Note survives restart.
- [ ] `201` includes `Location`; delete is bodyless `204`.
- [ ] Missing, validation, routing/method, and unexpected errors use the common envelope.
- [ ] Routers contain no SQL/transaction calls.
- [ ] Repositories contain no HTTP exceptions.
- [ ] Services own commits and rollback failed commits.
- [ ] Tests refuse database names without `_test`.
- [ ] Schema, service, repository, and API behavior are tested appropriately.
- [ ] `python -m pytest` passes.
- [ ] Logs identify requests without credentials or Note content.
- [ ] `.env` and credentials are absent from Git.

## What you should be able to explain after Phase 1

- Table, row, ORM model, Pydantic schema, and migration.
- Engine, pool, connection, Session, transaction, flush, commit, rollback, and refresh.
- Why SQLAlchemy was selected over SQLModel for this learning goal.
- Why migrations—not startup code—own schema evolution.
- Why Alembic autogenerate must be reviewed.
- Why create, update, and read use different schemas.
- Why PATCH distinguishes omitted from `null`.
- Why a service owns the use-case transaction.
- Why a repository must not raise an HTTP exception.
- Why PostgreSQL tests catch issues SQLite may hide.
- Why create returns `201 + Location`, update `200`, and delete bodyless `204`.
- How a request travels router -> service -> repository -> Session -> PostgreSQL and back.
- How to diagnose `422`, `404`, connection failure, constraint failure, and unexpected `500` differently.

---

# 7. Future-phase architectural preview

Everything in this section comes after the Phase 0–1 foundation. These summaries explain ordering and architectural intent; the [complete curriculum index](COMPLETE_BACKEND_CURRICULUM.md) links the authoritative step-by-step handbooks. Where a preview omits or differs from a detailed handbook, follow the detailed handbook and update this preview in your own architecture notes. For every implemented route, use Section 4’s worksheet and define every field, error, status, table, transaction, side effect, and test.

The ordering principle is:

```text
deterministic CRUD
  -> identity and ownership
  -> internal workflows and time
  -> unreliable/paid external calls
  -> controlled side effects
  -> memory and agent orchestration
  -> production operations
```

## Future Phase 2 — Authentication and ownership

### Outcome

Add users, secure password verification, sessions/tokens, a current-user dependency, and owner-scoped Notes. Do this before storing a user’s email, calendar, chat, memory, or provider credentials.

### Build order

1. Create `users`.
2. Register and log in.
3. Add a `get_current_user` dependency.
4. Add rotating refresh sessions and logout.
5. Migrate `notes` to include a non-null `user_id`.
6. Put ownership in every repository query.
7. Prove two-user isolation with tests.

Candidate tables:

- `users`: UUID ID, normalized unique email, password hash, display name, active flag, timestamps.
- `auth_sessions`: user ID, hashed opaque refresh token, expiry, revoked time, rotation/reuse metadata.
- `notes.user_id`: foreign key plus an index supporting `(user_id, created_at DESC)`.

For existing Notes, practice an expand/backfill/contract migration: add nullable `user_id`, create or choose an owner for old rows, backfill, verify no nulls, then make the column non-null. A one-step non-null migration fails when old rows exist.

Candidate endpoints:

| Route | Purpose | Main tables/tests |
|---|---|---|
| `POST /api/v1/auth/register` | Create account; `201` safe user response | `users`; normalized duplicate email `409`, password policy, stored hash differs from password |
| `POST /api/v1/auth/login` | Verify credentials; return short-lived access token and establish refresh session | `users`, `auth_sessions`; good/bad/inactive, generic invalid credentials, brute-force limits |
| `POST /api/v1/auth/refresh` | Rotate a valid refresh session | `auth_sessions`; expiry, revocation, old-token replay, concurrent reuse |
| `POST /api/v1/auth/logout` | Revoke session, clear refresh cookie; `204` | `auth_sessions`; refresh rejected afterward; repeated logout policy |
| `GET /api/v1/users/me` | Return current safe profile | `users`; valid/absent/expired access token |
| `PATCH /api/v1/users/me` | Change safe profile fields | `users`; empty/unknown/immutable fields |

Security rules:

- Hash passwords with a modern password hasher such as Argon2id. Hashing is one-way; do not reversibly encrypt passwords.
- Store only a hash of an opaque refresh token. Rotate on use and detect replay.
- A JWT is signed, not encrypted. Anyone holding it can usually inspect its claims.
- Keep access tokens short-lived and validate signature, expiry, subject, and intended issuer/audience as designed.
- Give wrong email and wrong password the same public error. Run a dummy password verification when the email is absent to reduce timing-based enumeration.
- Never log passwords, bearer tokens, cookies, hashes, or signing keys.
- For a browser frontend, one defensible design is a bearer access token plus a rotated opaque refresh token in an `HttpOnly`, `Secure`, carefully configured `SameSite` cookie. Cross-site cookie use also requires an explicit CSRF design.
- Restrict CORS to actual frontend origins. CORS is a browser rule, not authorization.
- A Note lookup must resemble `WHERE id=:id AND user_id=:current_user_id`; do not load by ID and hope a later layer remembers ownership.
- A well-formed ID owned by someone else can return `404` to avoid leaking its existence.

Exercises and checkpoint:

- Decode a JWT payload and explain why decoding does not verify it.
- Write the failing test where user B gets/patches/deletes user A’s Note before fixing ownership.
- Reuse an old refresh token after rotation and verify rejection.
- Inspect the ownership migration’s upgrade and downgrade.

Checkpoint: register two users, log in both, create Notes under both, prove cross-user access fails, rotate a refresh session, log out, rebuild from migrations, and pass all tests.

### What you should be able to explain after Future Phase 2

Authentication versus authorization; hashing versus encryption; access versus refresh token; `401` versus `403` versus privacy-preserving `404`; why UUID is not authorization; and why ownership belongs in SQL.

## Future Phase 3 — Tasks, reminders, time, and job foundations

### Outcome

Add related resources, filters, state transitions, due times, and the foundation for durable scheduled work without calling external notification providers yet.

Candidate data:

- `tasks`: user, title/description, `todo|in_progress|completed|cancelled`, priority, optional `due_at`, `completed_at`, timestamps.
- `reminders`: user, optional task FK, message, `remind_at`, original IANA time zone, schedule status `scheduled|triggered|cancelled`, `triggered_at`, timestamps.
- A later `notification_deliveries` table records per-channel `queued|sending|delivered|failed|dead_letter` outcomes. Keep “was this schedule triggered?” separate from “did Telegram deliver it?” A future generic job table records durable execution; do not rely on an in-memory timer.

Candidate endpoints:

| Route | Design focus |
|---|---|
| `POST /api/v1/tasks` | Defaults, aware due time, `201 + Location` |
| `GET /api/v1/tasks` | Status/priority/due filters, deterministic pagination |
| `GET/PATCH/DELETE /api/v1/tasks/{task_id}` | Ownership, state-transition table, FK behavior |
| `POST /api/v1/reminders` | Standalone or task-linked reminder, time-zone rules |
| `GET /api/v1/reminders` | Status/task/time-range filters and due-order index |
| `GET/PATCH/DELETE /api/v1/reminders/{reminder_id}` | Reschedule/cancel rules and triggered/terminal-state conflicts |
| `POST /api/v1/reminders/{reminder_id}/snooze` | A true command; define duration/new time and valid states |

Time rules:

- Accept only timezone-aware timestamps.
- Store instants in UTC; keep the IANA zone such as `Asia/Kolkata` when future recurrence or user presentation needs the original rules.
- A timezone is not merely a fixed offset; daylight-saving zones change rules by date.
- Draw allowed transitions before service code. A completed task should not silently become `in_progress` unless the contract permits reopening.
- Add indexes for real queries, for example due reminders by `(status, remind_at)`. Use `EXPLAIN` rather than “index everything.”
- FastAPI `BackgroundTasks` is not a durable future scheduler: a process restart can lose work. In Phase 3, a manually invoked “find due reminders” service may demonstrate trigger state only because notification deliveries do not exist yet. Phase 7 replaces that command: once deliveries exist, the service must lock the due schedule, select the complete verified/enabled channel set, insert every deterministic delivery, and mark the reminder triggered in **one transaction**. With zero channels or any insert failure, it must not mark the reminder triggered.
- At-least-once execution means a job may run again after a crash. Side effects need idempotency keys and delivery records.

Exercises and checkpoint:

- Draw task/reminder transition tables.
- Test an aware timestamp and reject a naive one.
- Test a daylight-saving boundary even if your own region does not use DST.
- Compare `EXPLAIN` before/after a due-reminder index.
- Prove an invalid task link leaves no Reminder row after rollback.

Checkpoint: task/reminder CRUD, ownership, filters, stable pagination, time semantics, transitions, constraints, migrations, and tests all work.

### What you should be able to explain after Future Phase 3

Foreign keys and delete behavior; state machines; UTC instant versus user time zone; application versus database invariant; composite index; durable job versus in-process background task; at-least-once execution and idempotency.

## Future Phase 4 — Conversations, chat, and a model gateway

### Outcome

Persist conversations/messages and call one model through a provider-neutral boundary. Begin with a deterministic fake so tests never require a paid/network call.

The small interface—not a giant AI framework—is the important abstraction:

```python
from typing import Protocol


class LLMClient(Protocol):
    def generate(self, request: "LLMRequest") -> "LLMResult": ...
```

The chat service depends on this protocol. A fake and vendor adapters implement it. Provider SDK types stay inside their adapter.

Candidate tables:

- `conversations`: user, title, timestamps, optional archive time.
- `messages`: conversation, constrained role, content, timestamp, optional model run.
- `model_runs`: user/conversation, provider, exact model, status, latency, token counts, cost in integer micros or exact numeric, provider request ID, sanitized error.

Candidate endpoints:

| Route | Design focus |
|---|---|
| `POST /api/v1/conversations` | Create resource |
| `GET /api/v1/conversations` | Owner-scoped cursor/page |
| `GET/DELETE /api/v1/conversations/{id}` | Archive/delete child policy |
| `POST /api/v1/conversations/{id}/messages` | Persist user message, invoke model, persist result/run; idempotency and failure |
| `GET /api/v1/conversations/{id}/messages` | Stable message ordering/pagination |

Reliability design:

- Persist the user message and a `started` run before the network call, then commit. Do not keep a database transaction open while waiting on a model provider.
- On provider failure, retain a failed run and return a safe code plus `run_id`.
- Add timeouts, input/output limits, per-user rate/cost budgets, and bounded retries only for classified transient failures. A retry can duplicate cost.
- Treat model output as untrusted text. It cannot grant tool permission.
- Decide whether prompt/output storage is necessary and how long it is retained. Default logs to metadata.
- Build a non-streaming response first. Add SSE only after specifying disconnect, cancellation, partial output, and persistence behavior.

Exercises and checkpoint:

- Implement a fake that returns predictable text and token counts.
- Simulate timeout and prove the failed run remains queryable.
- Ensure vendor imports exist only in the adapter.
- Send the same idempotency key twice and prove the paid action is not duplicated.

Checkpoint: conversations survive restart; fake and one real adapter follow one contract; errors/usage/latency are logged safely; automated tests never require paid access.

### What you should be able to explain after Future Phase 4

Port/adapter boundary; why network calls sit outside database transactions; timeout versus retry; idempotency; streaming tradeoffs; cost representation; and why model text is untrusted.

## Future Phase 5 — Tool registry, approvals, execution logs, and worker

### Outcome

Create the safety/audit foundation for an agent. Manual tool execution must work before a model may propose tools.

Keep tool implementations in an allowlisted code registry. Database metadata may enable/configure known tools, but a client must never submit an import path, shell command, SQL string, or arbitrary URL as a new executable “tool.”

Every definition has stable name/version, description, input/output schema, `read|write|destructive` risk, permission, timeout, and allowlisted handler key.

Candidate tables:

- `tool_definitions`: safe metadata, schemas as JSONB, risk, enabled flag, handler key/version.
- `tool_runs`: user/conversation, canonical validated input, status, requester (`human|model`), result, duration, idempotency key, sanitized error.
- `tool_run_decisions`: immutable approve/reject decision, user, exact input hash, reason, timestamp.
- Optional `agent_runs` and `agent_steps` only after manual runs work.

State machine:

```text
requested -> queued                         # policy-approved read
requested -> awaiting_approval
awaiting_approval -> queued | rejected
queued -> running | cancelled_before_start
running -> succeeded | failed | outcome_unknown
running -> cancel_requested -> succeeded | failed | outcome_unknown
```

`rejected`, `cancelled_before_start`, `failed`, and `succeeded` are terminal for one attempt. Cancellation after an external side effect begins is only a request: it cannot prove the provider did nothing. Use `outcome_unknown` when the worker cannot reconcile the result, then investigate/provider-query rather than relabeling it cancelled. A retry is a separately auditable attempt (or an explicit new transition only under a documented idempotent retry policy).

Candidate endpoints:

| Route | Design focus |
|---|---|
| `GET /api/v1/tools` and `GET /api/v1/tools/{name}` | Return only enabled/authorized safe metadata |
| `POST /api/v1/tool-runs` | Validate schema; return `202`; route by risk; idempotency |
| `GET /api/v1/tool-runs` and `GET /api/v1/tool-runs/{id}` | Owner-scoped, redacted audit history |
| `POST /api/v1/tool-runs/{id}/decisions` | Approve/reject exact version + canonical input hash; conflict after decision |
| `POST /api/v1/tool-runs/{id}/cancel` | Queued may become `cancelled_before_start`; running becomes `cancel_requested`, never proof that a side effect did not happen |
| `POST /api/v1/agent-runs` | Later: model can propose, never self-authorize, steps/tools under budgets |
| `GET /api/v1/agent-runs/{id}` | Poll partial/final state |

Safety and reliability:

- Never `eval`, `exec`, run arbitrary shell, or dynamically import client-supplied handlers.
- Validate model and human inputs identically.
- Read tools may qualify for automatic policy; writes default to confirmation; destructive actions need explicit recent approval.
- Bind approval to exact tool version and canonical input hash so the input cannot change afterward.
- Redact secrets from arguments, outputs, exceptions, and audit views.
- Enforce time/output limits and network allowlists.
- Retry a side-effecting handler only if its idempotency behavior is known.
- A worker claims queued rows with a lease. PostgreSQL `FOR UPDATE SKIP LOCKED` is a useful learning implementation; recover expired leases and promise at-least-once, not exactly-once.
- Introduce the common durable-work contract here. Either create a shared `work_items` table pointing to domain records or repeat equivalent fields on every executable run: status, idempotency key, attempt/max attempts, next-attempt time, lease owner/expiry, heartbeat, cancel-requested time, started/finished times, and sanitized last error. Tool runs, external actions, notifications, memory jobs, research jobs, and comparison runs must all implement this claim/recovery contract before a worker consumes them.

Exercises and checkpoint:

- Implement `list_tasks` as read-only and `create_task` as approval-required.
- Try changing input after approval and reject it.
- Run two workers and prove one row is claimed by one worker at a time.
- Crash a worker and recover an expired lease.

Checkpoint: definitions are discoverable, inputs validated, risky calls approval-gated, worker execution observable, and every outcome traceable.

### What you should be able to explain after Future Phase 5

Allowlist; tool schema; source-to-side-effect path; human approval; lease; worker; at-least-once delivery; idempotency; immutable audit decision; and why an LLM is never the authorization layer.

## Future Phase 6 — Google OAuth, Calendar, then Gmail

### Outcome

Link a user-controlled Google account using delegated authorization. Start with the least risky read operations, then controlled drafts and writes:

```text
connect Google
  -> Calendar list
  -> Calendar create/update with explicit action
  -> Gmail read-only
  -> Gmail draft
  -> Gmail send with explicit confirmation and idempotent audit
```

Google OAuth authorizes access to Google data; it is distinct from authenticating a user to your own API.

Candidate tables:

- `integration_connections`: user/provider/provider account, granted scopes, encrypted refresh token, token/status metadata, timestamps. Never expose token columns.
- `oauth_transactions`: hashed one-time state, PKCE verifier, user, requested scopes, exact redirect target, expiry, used time.
- `external_action_runs`: connection, operation, canonical input hash, provider resource ID, plus the complete Phase 5 durable-work claim/lease/retry/idempotency fields and sanitized error.

Do not mirror an entire mailbox initially. Read on demand and persist only explicit drafts, action logs, provider IDs, or future sync cursors with a defined need.

Candidate endpoints:

| Route | Purpose/design focus |
|---|---|
| `POST /api/v1/integrations/google/authorization-requests` | Create one-time state/PKCE and return authorization URL for explicitly requested features/scopes |
| `GET /api/v1/oauth/callbacks/google?code=&state=&error=` | Handle approval **or denial/error**; atomically consume valid one-time state, exchange a code when present, store safely, then use a fixed safe redirect; never put tokens/provider errors in redirect |
| `GET /api/v1/integrations` | Safe connection metadata only |
| `DELETE /api/v1/integrations/{connection_id}` | Atomically disable local use, erase connection ciphertext, cancel/recheck dispatchable work, and persist encrypted revocation intent; a bounded worker retries provider revocation and wipes its token copy |
| `GET /api/v1/calendar/events` | Connection/calendar/time range/pagination; normalize provider representation |
| `POST /api/v1/calendar/events` | Create event with aware start/end/time zone; idempotency and provider ID |
| `PATCH /api/v1/calendar/events/{provider_event_id}` | Partial update, ownership, ETag/version conflict if supported |
| `GET /api/v1/email/messages` | Gmail query/page through owner’s connection |
| `GET /api/v1/email/messages/{provider_message_id}` | Normalize MIME/header/body representation safely |
| `POST /api/v1/email/drafts` | Create new/reply draft with correct recipients/thread headers |
| `POST /api/v1/email/drafts/{provider_draft_id}/send` | Explicitly queue an existing draft; return `202`; one send per idempotency key |

Provider-neutral internal paths such as `/email` and `/calendar` let a future Outlook adapter fit the same application contract. `connection_id` selects the user-owned account; provider IDs remain opaque strings.

Security/reliability rules:

- Use authorization-code flow, one-time unpredictable `state`, PKCE, and exact redirect URIs.
- Ask for offline access when the backend must act after the browser session. Follow Google’s current consent rules rather than assuming every exchange returns a refresh token.
- Atomically validate and consume state with expiry, user, provider, redirect, and PKCE binding. Treat callback denial, missing code, provider error, expired state, and replay as explicit safe outcomes.
- Request scopes incrementally. Do not request Gmail send merely to list messages.
- Store the **actually granted** scopes and gate each capability from that set. Partial consent is not full success and should not become an unrelated `500`.
- Encrypt refresh tokens at rest with a key stored separately from the database. Plan key rotation.
- Google may omit a new refresh token on later grants. Preserve an existing valid encrypted refresh token when the response omits it; never overwrite it with `null`. Obtain renewed consent only through the documented flow.
- Never return/log client secrets, authorization codes, access tokens, refresh tokens, cookies, or raw provider error payloads.
- Handle revoked/expired grants as a stable “reauthorization required” state, not a mysterious `500`.
- Set timeouts; honor provider `Retry-After`; retry classified transient failures with bounded exponential backoff and jitter.
- External writes need idempotency records. A client timeout does not prove Google failed.
- Use provider ETags/version values where available to prevent overwriting another edit.
- Test service code with fake adapters. Keep live-provider tests manual or separately marked against a dedicated test account.
- Drafting and sending are different trust boundaries. An agent may propose or draft; sending requires the user/policy decision established in Phase 5.
- Minimize retention of full email bodies and attendee data.

Exercises and checkpoint:

- Draw browser -> backend -> Google authorization server -> Google resource server.
- Replay an OAuth state and reject it.
- Simulate the user denying consent and return to the frontend without leaking the raw provider error.
- Return only a subset of requested scopes and prove unavailable features stay disabled.
- Exchange a later authorization code whose response omits `refresh_token` and prove the stored token is preserved.
- Revoke the Google grant and return a stable reauthorization error.
- Fake a Calendar `429` and honor `Retry-After`.
- Prove duplicate send requests create one external action.
- Prove a model-proposed reply cannot skip the approval decision.

Checkpoint: connect/disconnect Google; list/create/update Calendar events; read Gmail; create a reply draft; explicitly queue one audited, idempotent send; test without live credentials by default.

### What you should be able to explain after Future Phase 6

OAuth roles; authorization code; state; PKCE; scopes; access versus refresh token; token encryption; adapter; rate limiting; ETag; provider ID; and why draft/send are separate.

## Future Phase 7 — Telegram, email, Slack notifications and voice

### Outcome

Turn due reminders into durable delivery records across channels. Then let audio become a transcript and proposed action—not an automatically authorized command.

Candidate notification tables:

- `notification_channels`: user, `telegram|email|slack`, safe destination metadata, `pending_verification|verified|disabled`, verified timestamp, encrypted secret/config if unavoidable.
- `channel_verification_challenges`: channel, hashed one-time code/nonce, expiry, attempt count, consumed time. A Slack OAuth installation can instead produce a verified channel through its signed callback flow.
- `notification_deliveries`: user/channel/reminder, event/payload, provider ID, plus the complete Phase 5 durable-work claim/lease/retry/idempotency fields and sanitized error.
- `webhook_events`: provider event ID unique, received/processed timestamps, safe payload subset or hash.

Candidate notification endpoints:

| Route | Design focus |
|---|---|
| `POST/GET /api/v1/notification-channels` | Configure/list safe metadata; credential input is write-only |
| `PATCH/DELETE /api/v1/notification-channels/{id}` | Enable/disable/disconnect, ownership, secret deletion |
| `POST /api/v1/notification-channels/{id}/verification-challenges` | Issue/rotate an expiring one-time email code or Telegram pairing nonce; rate-limit and never return a stored hash |
| `POST /api/v1/notification-channels/{id}/verify` | Consume the exact challenge; wrong/expired/replayed input is rejected; mark channel verified once |
| `POST /api/v1/notification-deliveries` | Queue delivery; `202`; channel state and idempotency |
| `GET /api/v1/notification-deliveries/{id}` | Poll safe status/error |
| `POST /api/v1/webhooks/telegram/{opaque_path}` | Verify configured secret header, deduplicate update, acknowledge quickly |
| `POST /api/v1/webhooks/slack/events` | Verify Slack signature/timestamp and deduplicate; handle challenge if used |

A reminder worker atomically claims due reminders and creates delivery rows. Delivery is at least once: a crash after provider success but before local status update can cause a retry, so use provider idempotency when offered and local keys/status checks when not.

If you later accept Telegram or Slack text commands, normalize each verified inbound event into a stored command and the same proposed-tool/approval workflow from Phase 5. A webhook payload must never jump directly to a side effect.

Notification rules:

- Never deliver through an unverified channel. Email proves code possession; Telegram pairs a signed bot event such as `/connect <nonce>`; Slack commonly uses an OAuth installation or otherwise a documented signed setup flow.
- Verify webhook secrets/signatures and reject stale replays.
- Acknowledge webhooks quickly, then process in the worker.
- Honor `429`/`Retry-After`; cap attempts and preserve a failed/dead-letter state.
- Telegram bot tokens, Slack webhook URLs/tokens, and SMTP/API keys are secrets.
- Validate user-supplied callback URLs against SSRF, or prefer provider OAuth/known domains.
- Notifications should not include sensitive memory/chat/email content by default.

For voice, do not store large audio blobs in PostgreSQL. In development use a controlled directory; later use object storage and persist metadata.

Candidate voice data/endpoints:

- `voice_commands`: user, object key, transcript, language, status, interpreted intent, proposed tool action IDs, decision timestamps, retention/deletion time, sanitized error.
- `POST /api/v1/voice-commands`: multipart audio upload; return `202`; enforce type/size/duration/quota.
- `GET /api/v1/voice-commands/{id}`: poll transcript and proposed actions.
- `POST /api/v1/tool-runs/{run_id}/decisions`: approve any linked voice proposal through the single Phase 5 approval contract; do not add a parallel voice decision route.
- `DELETE /api/v1/voice-commands/{id}/audio`: remove retained audio while optionally retaining transcript according to policy.

Validate MIME type **and** file signature, duration, size, and randomized storage name. Never trust an uploaded filename. Transcription can misunderstand “delete all tasks”; it proposes, but never authorizes.

Exercises and checkpoint:

- Force channel `429` and observe delayed retry.
- Deliver one webhook twice and process it once.
- Reject wrong, expired, and replayed channel-verification challenges; prove an unverified channel cannot queue delivery.
- Simulate crash after provider success and explain duplicate control.
- Upload a renamed non-audio file and reject it.
- Transcribe a destructive phrase and prove it remains awaiting approval.

Checkpoint: due reminders create observable, retryable delivery records through at least one channel; webhook verification/deduplication work; voice yields a transcript and safe proposed actions with deletion/retention controls.

### What you should be able to explain after Future Phase 7

Delivery/outbox record; at-least-once; retry/backoff/dead letter; webhook authentication/replay; upload validation/object storage; transcription; and why voice recognition is not authorization.

## Future Phase 8 — Persistent memory and retrieval

### Outcome

Store explicit user facts, preferences, project notes, and conversation summaries with provenance and deletion controls. Start with SQL and lexical search; add embeddings only after a measured retrieval need.

Candidate data:

- `memory_items`: user, `fact|preference|project_note|conversation_summary`, content, source type/ID, confidence, importance, content hash, `proposed|active|rejected|archived`, validity/archive timestamps. Explicit user saves begin `active`; inferred items begin `proposed`.
- `memory_sources`: explicit provenance when one memory derives from several records.
- `memory_decisions`: proposed item, accepting/rejecting user, exact content hash/version, decision, reason, timestamp.
- `memory_jobs`: user, `summarize|reindex`, target ID, `queued|running|succeeded|failed|cancelled`, idempotency key, attempt/lease owner/lease expiry, next attempt, sanitized error, timestamps.
- Optional `memory_embeddings`: memory ID, embedding provider/model/version, vector, content hash, indexed time.

Candidate endpoints:

| Route | Design focus |
|---|---|
| `POST /api/v1/memories` | Explicit save; provenance, type, duplicate policy; `201` |
| `GET /api/v1/memories` | Kind/archive/query filters and owner-scoped pagination |
| `GET/PATCH/DELETE /api/v1/memories/{id}` | Editing, contradiction/archive policy, deletion cascading to indexes |
| `POST /api/v1/memories/{id}/decisions` | Accept/reject one proposed version; immutable decision; conflict after decision/content change |
| `POST /api/v1/memory-searches` | Complex read query with kinds/limit/minimum score; ranked results plus provenance |
| `POST /api/v1/conversations/{id}/summary-jobs` | Queue summary extraction; inferred output proposed or marked clearly |
| `GET /api/v1/memory-jobs/{id}` | Poll summary/reindex status |

Memory rules:

- Default to explicit user-saved memory. Machine-inferred memory is `proposed` and is excluded from normal retrieval until an explicit decision makes it `active`.
- Summary/reindex jobs use the same lease, retry, idempotency, cancellation, and safe-error principles as other durable jobs; a status endpoint requires a persisted model, not an in-memory task.
- Do not silently persist passwords, tokens, financial information, or every conversation forever.
- Memory text is untrusted data, not executable instructions.
- Retrieve a small scored context with provenance; do not shovel all memory into every prompt.
- Define conflict behavior. A changed preference can archive the old value without pretending both are current.
- Deletion removes lexical/vector indexes and caches.
- Version embedding model and indexed content hash so stale vectors can be detected/rebuilt.
- Create a labeled query set and measure recall-at-k/precision. “Feels relevant” is not an evaluation.
- An embedding is a numerical retrieval index, not proof that a statement is true.

Exercises and checkpoint:

- Create contradictory preferences and define which is current.
- Label ten memory queries and calculate simple recall at k.
- Delete one item and prove it cannot return through lexical or vector paths.
- Inspect the exact limited memory context supplied to chat.

Checkpoint: memories can be explicitly saved, filtered/searched, updated, archived/deleted, and selectively injected with provenance; retrieval has a basic evaluation set.

### What you should be able to explain after Future Phase 8

Chat history versus memory; provenance; explicit versus inferred memory; lexical versus semantic search; embedding/version; deduplication; retention/deletion; and retrieval evaluation.

## Future Phase 9 — Deep research jobs, sources, and citations

### Outcome

Run bounded asynchronous research that records sources and links individual claims/citations to evidence. A long research task must not hold one HTTP request open.

Candidate tables:

- `research_jobs`: user, query, constraints/budget, plan, final answer, provider/model, plus the complete Phase 5 durable-work claim/lease/retry/idempotency fields, timestamps, and sanitized error.
- `research_steps`: job, sequence/kind/status, safe summarized input/output, timing/error.
- `research_sources`: job FK, canonical URL, title/publisher, published/retrieved times, content hash, permitted excerpt/metadata; unique `(job_id, id)` identity available for citation constraints.
- `research_citations`: job/source, stable label, supported claim or answer-span metadata, short evidence excerpt. Use a composite FK `(job_id, source_id) -> research_sources(job_id, id)` so a citation cannot silently point to another job’s evidence.

Candidate endpoints:

| Route | Design focus |
|---|---|
| `POST /api/v1/research-jobs` | Validate query/source/time/cost budgets; idempotency; return `202 + Location` |
| `GET /api/v1/research-jobs` | Owner-scoped history/status filter |
| `GET /api/v1/research-jobs/{id}` | Poll queued/running/partial/failed/completed state |
| `GET /api/v1/research-jobs/{id}/sources` | Source metadata and citation mapping |
| `POST /api/v1/research-jobs/{id}/cancel` | Request cancellation; terminal conflict behavior |
| `GET /api/v1/research-jobs/{id}/events` | Persisted SSE projection built after polling; reconnect/event cursor policy |

Security/reliability rules:

- Web content is hostile input. Text saying “ignore previous instructions” remains data and cannot grant permissions.
- Defend fetchers against SSRF: expected schemes, no URL credentials, blocked loopback/private/link-local/reserved destinations, redirect revalidation, DNS-rebinding awareness, byte/time/redirect limits.
- Cap sources, pages, content bytes, tokens, time, concurrent calls, and cost.
- Canonicalize/deduplicate URLs and content hashes.
- Store checkpoints so a worker can resume after failure; cancellation prevents new steps and cleans up controlled resources.
- A bibliography is not sufficient. Each citation must actually support its associated claim.
- Enforce same-job citation/source identity in PostgreSQL as well as service code; authorization checks alone do not protect referential truth.
- Preserve retrieval time because pages change.
- Do not store entire copyrighted pages merely because a fetcher saw them. Store necessary metadata/permitted evidence and follow access/usage rules.

Exercises and checkpoint:

- Feed prompt-injection text as a source and prove it cannot call tools.
- Make a citation point to an unrelated source and reject it in validation/evaluation.
- Cancel halfway and ensure no new steps begin.
- Deduplicate URL variants with tracking parameters.
- Resume from a stored checkpoint after a worker crash.

Checkpoint: a bounded background job returns an answer whose citations trace to stored source metadata/evidence; timeout, cancellation, partial failure, SSRF, and prompt-injection paths are tested.

### What you should be able to explain after Future Phase 9

Why long work returns `202`; polling versus SSE; checkpoint/resume/cancel; SSRF; retrieved prompt injection; source versus citation; bounded budget; and evidence provenance.

## Future Phase 10 — Model comparison and evaluation

### Outcome

Run the same versioned input across multiple provider adapters and record output, latency, usage, cost, failures, and ratings fairly. This becomes meaningful only after representative chat/research workloads exist.

Candidate tables:

- `model_configs`: provider, exact model/snapshot, allowed parameters/capabilities, active flag; never API secrets.
- `comparison_runs`: user, prompt/test-case reference, repetitions, **immutable canonical execution-spec snapshot and hash**, plus the complete Phase 5 durable-work claim/lease/retry/idempotency fields and timestamps.
- `comparison_results`: run/config, output, latency/time-to-first-token, tokens, exact cost, provider request ID, status/error.
- `result_ratings`: result, `human|rule|model` evaluator, score/rationale, evaluator version.
- Later `eval_suites` and `eval_cases` for repeatable regression sets.

Candidate endpoints:

| Route | Design focus |
|---|---|
| `GET /api/v1/model-configs` | Safe enabled configurations/capabilities |
| `POST /api/v1/model-comparisons` | Prompt + configs + repetitions; budget/idempotency; return `202` |
| `GET /api/v1/model-comparisons` | Owner-scoped history |
| `GET /api/v1/model-comparisons/{id}` | Aggregate/partial status |
| `GET /api/v1/model-comparisons/{id}/results` | Comparable measured results, stable/blinded ordering |
| `POST /api/v1/model-comparison-results/{id}/ratings` | Human/rule/evaluator score with versioned rubric |
| `POST /api/v1/eval-runs` | Later run a versioned suite across configs; return `202` |

Fairness/reliability rules:

- Snapshot the exact system policy, ordered messages/user input, retrieval item IDs/content hashes, tool schema versions, sampling parameters, output limits, adapter version, and requested model configuration. Hash a canonical serialization. A later edit to `model_configs` must not rewrite what an old run means.
- Keep that execution spec as equivalent as provider capabilities allow; record every adapter translation or unsupported difference and the exact model identifier the provider reports.
- Pin exact model versions/snapshots when offered.
- Repeat representative cases because outputs are nondeterministic.
- Randomize/blind human display order to reduce preference bias.
- Separate measured latency/cost from judged quality.
- Store cost as integer micros or exact `NUMERIC`, not binary float.
- Record evaluator model/version/rubric; an LLM judge is not ground truth.
- Let one provider fail without erasing successful results from others.
- Enforce concurrency and spend caps and never allow arbitrary provider base URLs.
- Do not claim a universal “best model” from one prompt.

Exercises and checkpoint:

- Repeat one prompt and quantify output/latency variance.
- Make one provider fail while others complete.
- Add a deterministic exact-match or schema grader and a human rating.
- Demonstrate a float rounding issue for currency and fix representation.

Checkpoint: a representative input/eval suite runs through several adapters with immutable, reconstructable configuration and comparable output, latency, token, cost, failure, and rating records. Reconstruction improves auditability; stochastic output is still not guaranteed to repeat byte for byte.

### What you should be able to explain after Future Phase 10

Quality/latency/cost tradeoff; reproducibility; nondeterminism; snapshot; evaluation rubric/bias; partial failure; exact money representation; and why telemetry is provider-neutral.

## Future Phase 11 — Docker and production hardening

### Outcome

Containerize a locally understood application, add CI/observability, and prove deployment/recovery behavior. Docker packages the system; it does not fix an unclear one.

Initial runtime shape:

- one stateless FastAPI web service;
- a pinned PostgreSQL major/image for local Compose, backed by an explicit named volume; production may instead use a managed PostgreSQL service;
- one worker using the same application image with a different command;
- one one-shot migration/release job;
- a maintained platform TLS terminator/reverse proxy in every non-local environment;
- Redis only if a measured need or chosen durable queue requires it.

Do not have every web replica race to run `alembic upgrade head`. Run migrations once as a controlled release step.

Operational endpoints:

| Route | Contract |
|---|---|
| `GET /health/live` | `200` if process/event loop can answer; do not query dependencies |
| `GET /health/ready` | `200` only when DB is reachable and migration revision compatible; sanitized `503` otherwise |
| `GET /metrics` | Protected internal metrics, never a public user endpoint |

Production checklist:

- Small pinned base image, `.dockerignore`, non-root user, no development reload.
- Pin the PostgreSQL major version, declare the local named volume, document upgrades, and never treat that volume as a backup. Production data needs durable platform storage plus scheduled backups with retention.
- Secrets from the deployment secret mechanism, not an image or Compose file committed to Git.
- Structured logs with request/trace ID, route, status, latency, and job/run ID; never authorization headers or sensitive bodies.
- DB connection-pool limits, statement/network timeouts, and graceful shutdown.
- CI runs formatting/lint, type checks, unit tests, PostgreSQL integration tests, blank-database migration, and migration drift checks.
- Expand/contract migrations and an explicit rollback or forward-fix plan.
- Backups plus an actual restore drill. A backup never restored is only a hope.
- Metrics for request errors/latency, pool use, provider errors/rate limits, queue depth/age, retries, and spend.
- Rate limits and per-user budgets.
- TLS, exact CORS origins, trusted proxy/host configuration, secure cookies.
- Dependency update/scanning process.
- Load-test actual slow paths and profile before optimizing.

Exercises and checkpoint:

- Build from a clean clone with no host Python packages.
- Start PostgreSQL, migration job, API, and worker via Compose.
- Kill/restart a worker during a job and inspect lease recovery.
- Break DB connectivity: liveness stays healthy while readiness fails.
- Restore a backup into a fresh database and run smoke tests.

Checkpoint: a clean machine can start the stack, migrate blank data, pass tests, survive worker restart, report readiness correctly, and restore data.

### What you should be able to explain after Future Phase 11

Image versus container; Compose service; stateless web process versus worker; release migration; liveness/readiness; graceful shutdown; structured logs/metrics/traces; backup/restore; and why “server starts” is not production readiness.

---

# 8. How to think like a backend engineer

The important upgrade is not memorizing FastAPI decorators. It is learning to ask the questions that make systems predictable.

## 8.1 Mental models

### The API is a contract

A caller depends on documented behavior, not your implementation. Renaming a private function is usually harmless; changing a response field, status, null rule, or ordering can break clients. Design errors and pagination as deliberately as happy-path JSON.

### A request is a pipeline

Think in stages:

```text
route match
  -> authentication
  -> parsing/validation
  -> use-case rules
  -> transaction and/or provider call
  -> serialization
  -> logging/metrics
```

When something fails, locate the stage before editing.

### The database is shared state

A table is not just Python objects stored elsewhere. Multiple requests and workers can read/write concurrently. Every write raises questions about constraints, transaction scope, isolation, lost updates, indexes, migrations, and recovery.

### A transaction is one business promise

Put operations in one transaction when the user’s promise is “all must happen or none should.” Do not stretch a transaction over a slow external API call. For database + external side effects, use durable action/outbox records and idempotent workers instead of pretending two independent systems share one atomic commit.

### Validate at every trust boundary

- Pydantic protects the HTTP boundary and gives useful client errors.
- Service rules protect use-case semantics.
- Database constraints protect persistent truth across all writers.
- Adapter schemas validate provider inputs and untrusted provider outputs.
- Authorization protects each operation on each resource.

One layer does not make the others redundant.

### Errors are outputs

Expected absence, conflict, invalid state, rate limit, and provider timeout are part of the API contract. Unexpected exceptions are logged with internal evidence and returned as a safe generic failure. Never catch `Exception` merely to return `200` or hide a bug.

### External systems always fail

For every provider call, decide:

- timeout;
- transient versus permanent failure;
- retry count/backoff/jitter;
- rate-limit behavior;
- idempotency/duplicate effect;
- authentication expiry/revocation;
- partial success;
- audit/observability;
- what the caller sees.

### Side effects need identity

Email sends, calendar writes, tool calls, research runs, and notifications need a run/action ID and usually an idempotency key. Without an identity you cannot answer “did it happen?”, retry safely, or audit it.

### Dependencies have lifetimes

Settings and Engine are process-scoped. A Session is request-scoped. A database transaction is use-case-scoped. A provider client may be process- or request-scoped depending on its documented safety. Explicit lifetimes prevent leaks and concurrency bugs.

### Async is a tool, not a speed adjective

Async helps concurrency while waiting on async-capable I/O. It does not make CPU work faster and can be harmed by blocking calls. Measure latency, throughput, pool use, and real bottlenecks before rewriting.

### Tests are executable contracts

Test what a caller or database can observe. Mock a provider boundary; do not mock every private call. A test that asserts implementation sequence without contractual need makes refactoring expensive and proves little.

### Logs are evidence, not storage

Record event names, IDs, state, duration, and safe errors. Avoid complete payloads and secrets. A request ID connects client report, API log, provider action, and job execution.

### Security is resource-specific

Always ask: **which authenticated actor may perform which operation on which specific resource, under what state and approval?** Authentication alone is not security. A random UUID is not permission.

### Abstractions must earn their cost

Write explicit Notes code first. Generalize after multiple real modules reveal the same stable pattern. Premature generic repositories and “base CRUD services” often hide query/transaction differences precisely when you need to understand them.

## 8.2 A design review you can perform alone

Before implementing a change, answer:

1. What caller problem is being solved?
2. What is the observable contract?
3. Which data is trusted and which is untrusted?
4. Who owns the data and how is authorization enforced in the query?
5. What must be atomic?
6. What happens if the process/database/provider fails after each step?
7. Can a retry duplicate a side effect?
8. What constraint protects truth if this code is bypassed?
9. What query/index/migration does this add?
10. What will logs and metrics show without revealing sensitive data?
11. How will this be tested at the cheapest truthful level?
12. How will an old client or old database version behave?
13. How can the user export/delete sensitive data later?

If several answers are “not sure,” reduce the slice and research before coding.

## 8.3 Debugging decision tree

```text
Process does not start
  -> interpreter/environment/import/config/port

404 or 405
  -> registered path, prefix, method, trailing slash

422
  -> path/query/body schema and missing/null/extra semantics

500 with database exception
  -> configured database, migration revision, constraint, transaction state, SQL

Wrong successful data
  -> contract expectation, service rule, repository filter/order, transaction/refresh

Slow request
  -> measure route/service/query/provider; inspect query plan and pool; then optimize

Intermittent external failure
  -> timeout, rate limit, retry classification, idempotency, provider status/request ID
```

Do not change several layers at once. Preserve the original evidence, form one falsifiable hypothesis, and add a regression test.

---

# 9. How to read official documentation effectively

## 9.1 The question-driven reading loop

Do not try to read every documentation site front to back. Use this loop:

1. Write one precise question: “When does a SQLAlchemy Session begin a transaction?”
2. Check your installed major version with `python -m pip show <package>`.
3. Read the canonical tutorial/explanation for that version.
4. Pay special attention to notes, warnings, defaults, and lifecycle statements.
5. Predict the answer before running code.
6. Reproduce the smallest official example in a scratch file/database.
7. Change one variable and predict again.
8. Apply the idea to one vertical slice.
9. Add a test.
10. Write your conclusion and link in `docs/learning-journal.md`.

Tutorials build the mental model. How-to guides solve a task. Reference pages answer exact symbol/parameter questions. Release/migration notes explain version differences. Use the right document type.

When reading a copied example, explain:

- every import;
- which object creates/owns which resource;
- object lifetime;
- failure behavior;
- default values;
- what is omitted for brevity;
- which part is example-only versus appropriate for your system.

For an error, search the exact exception text, but first identify the first frame in your own project. Prefer official documentation, standards, and maintained primary sources over random snippets.

## 9.2 Phase 0 and HTTP reading

- [Python virtual environments](https://docs.python.org/3/tutorial/venv.html)
- [Python typing](https://docs.python.org/3/library/typing.html)
- [Python logging HOWTO](https://docs.python.org/3/howto/logging.html)
- [FastAPI tutorial](https://fastapi.tiangolo.com/tutorial/)
- [FastAPI first steps](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI request bodies](https://fastapi.tiangolo.com/tutorial/body/)
- [FastAPI response status codes](https://fastapi.tiangolo.com/tutorial/response-status-code/)
- [FastAPI async/concurrency explanation](https://fastapi.tiangolo.com/async/)
- [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
- [RFC 5789: PATCH](https://www.rfc-editor.org/rfc/rfc5789.html)
- [RFC 9457: Problem Details](https://www.rfc-editor.org/rfc/rfc9457.html) — compare this standard with the guide’s simpler custom error envelope; do not claim RFC compliance unless you adopt its fields/media type.

## 9.3 Phase 1 reading

- [PostgreSQL tutorial](https://www.postgresql.org/docs/current/tutorial.html)
- [PostgreSQL transactions](https://www.postgresql.org/docs/current/tutorial-transactions.html)
- [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html)
- [PostgreSQL indexes](https://www.postgresql.org/docs/current/indexes.html)
- [PostgreSQL `EXPLAIN`](https://www.postgresql.org/docs/current/using-explain.html)
- [SQLAlchemy 2.0 unified tutorial](https://docs.sqlalchemy.org/en/20/tutorial/)
- [SQLAlchemy ORM quick start](https://docs.sqlalchemy.org/en/20/orm/quickstart.html)
- [SQLAlchemy Session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- [SQLAlchemy ORM querying guide](https://docs.sqlalchemy.org/en/20/orm/queryguide/)
- [SQLAlchemy Engine configuration](https://docs.sqlalchemy.org/en/20/core/engines.html)
- [SQLAlchemy column INSERT/UPDATE defaults](https://docs.sqlalchemy.org/en/20/core/defaults.html)
- [SQLAlchemy PostgreSQL/Psycopg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
- [Psycopg basic usage](https://www.psycopg.org/psycopg3/docs/basic/usage.html)
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Alembic autogenerate and its limits](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
- [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/)
- [Pydantic standard date/time types](https://docs.pydantic.dev/latest/api/standard_library_types/#datetimes)
- [Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/)
- [Pydantic settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [FastAPI dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [FastAPI error handling](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [FastAPI bigger applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [pytest getting started](https://docs.pytest.org/en/stable/getting-started.html)
- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)

## 9.4 Future security and integration reading

- [FastAPI security](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI OAuth2/JWT teaching example](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [OWASP API Security Project](https://owasp.org/www-project-api-security/)
- [OAuth 2.0 Authorization Framework, RFC 6749](https://www.rfc-editor.org/rfc/rfc6749.html)
- [OAuth 2.0 PKCE, RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html)
- [OAuth 2.0 Security Best Current Practice, RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)
- [Google OAuth 2.0 overview](https://developers.google.com/identity/protocols/oauth2)
- [Google OAuth for web-server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google OAuth security best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
- [List Gmail messages](https://developers.google.com/workspace/gmail/api/guides/list-messages)
- [Gmail drafts](https://developers.google.com/workspace/gmail/api/guides/drafts)
- [Google Calendar API overview](https://developers.google.com/workspace/calendar/api/guides/overview)
- [Create Google Calendar events](https://developers.google.com/workspace/calendar/api/guides/create-events)
- [Slack Web API](https://api.slack.com/web)
- [Slack: verifying requests](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
- [Slack incoming webhooks](https://api.slack.com/messaging/webhooks)
- [Slack Web API rate limits](https://api.slack.com/apis/rate-limits)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Docker getting started](https://docs.docker.com/get-started/)
- [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [Compose services reference](https://docs.docker.com/reference/compose-file/services/)

Provider capabilities, model names, prices, rate limits, OAuth scopes, and policies change. At the start of Phases 4, 8, 9, and 10—and Phase 7 for transcription—add the chosen provider’s official quickstart, tool-calling/structured-output reference, embeddings reference, transcription reference, research/search reference, evaluation guide, live model catalog, pricing, capability, and rate-limit pages to your journal as applicable. Record the exact adapter/model/runtime metadata. Do not freeze guesses or third-party blog summaries into domain logic.

---

# 10. Final seven-day execution plan

Treat this as an **intensive first sprint**, not a deadline for your understanding. Assume roughly two to four focused hours per “day,” and repeat a day over several sessions whenever its checkpoint is not yet true. Never compensate by copying unexplained code. The seven-day finish line is the production-shaped Notes backend from Phases 0–1, not the full multi-month AI workspace.

## Day 1 — Walking skeleton

Build:

- Initialize Git, `.venv`, `pyproject.toml`, ignore files, README, and learning journal.
- Install Phase 0 dependencies.
- Write the `/health` worksheet.
- Implement/test `/health`.
- Inspect `/docs` and `/openapi.json`.

Exercises:

- Call health using browser, Swagger, and `curl -i`.
- Deliberately fail the test and one import.
- Draw Uvicorn -> FastAPI -> route -> response.

Day checkpoint: a new terminal can follow your README and produce a green health test.

Teach back: ASGI, Uvicorn, FastAPI, Pydantic, JSON, route decorator, process/host/port, virtual environment.

## Day 2 — Contracts and Pydantic

Build/design:

- Complete all five Notes endpoint worksheets **before persistence implementation**.
- Decide title/content/null limits, list shape/order/pagination, error envelope, and exact statuses.
- Implement and unit-test `NoteCreate`, `NoteUpdate`, `NoteRead`, and list schemas.

Exercises:

- Test whitespace, boundaries, unexpected fields, empty PATCH, omitted content, null content, and null title.
- Explain `POST` versus `PATCH`, `201` versus `200`, and `404` versus `422`.

Day checkpoint: schemas/tests encode every body rule; you can defend each method/status.

Teach back: API contract, schema, validation, missing versus null, request versus response model.

## Day 3 — PostgreSQL, SQLAlchemy, and Alembic

Build:

- Install/start PostgreSQL; create app role, dev DB, test DB.
- Practice manual SQL in a transaction.
- Add settings, Engine, SessionFactory, request Session.
- Define `Note` ORM model.
- Initialize Alembic, generate/review/apply migration.

Exercises:

- Predict the table DDL and inspect `\d+ notes`.
- Cause the title check constraint to reject a row.
- Downgrade/upgrade only a disposable DB.

Day checkpoint: a blank DB reaches Alembic head; no `create_all` exists.

Teach back: table/model/migration; Engine/pool/connection/Session; flush/commit/rollback; Pydantic rule versus DB constraint.

## Day 4 — First vertical slices

Build:

- Add repository/service/dependency/router for create.
- Write create API tests and inspect the inserted row.
- Implement retrieve plus `NoteNotFoundError` translation.
- Add `201`, `Location`, `200`, `404`, and validation behavior.

Exercises:

- Trace one success and one missing Note through every layer.
- Restart the server and retrieve the row.
- Move a line to the wrong layer, describe the consequence, and restore it.

Day checkpoint: create/retrieve work manually and in tests against PostgreSQL.

Teach back: router/service/repository boundary, dependency injection, transaction owner, response model.

## Day 5 — Complete CRUD and errors

Build:

- Add deterministic list + pagination/count.
- Add PATCH using `exclude_unset=True` and the mapped database-clock `updated_at`.
- Add bodyless DELETE.
- Normalize validation, routing/method, and unexpected errors.
- Add request IDs and safe completion logs.

Exercises:

- Remove `exclude_unset` and reproduce the bug.
- Try every endpoint-card boundary manually.
- Compare malformed UUID (`422`) and missing UUID (`404`).

Day checkpoint: all five endpoint contracts work and Swagger reflects them.

Teach back: stable ordering, pagination tradeoff, error code/message/details/request ID, fat controller smell.

## Day 6 — Test and debug like an engineer

Build:

- Complete schema and service unit tests.
- Complete API/repository integration tests on the guarded test DB.
- Ensure Alembic upgrades the test DB.
- Add a regression test for each bug found.

Debug labs:

- Broken DB port/URL.
- Missing migration/undefined table on a disposable DB.
- Forced transaction failure and rollback.
- Temporary SQL logging followed by cleanup.

Day checkpoint: tests are isolated, test DB guard works, logs are useful and safe, full suite is green.

Teach back: unit versus integration test, fixture, Arrange–Act–Assert, traceback workflow, why PostgreSQL instead of SQLite.

## Day 7 — Clean-room verification and teach-back

Verify from scratch:

1. Follow only the README from a fresh environment/clone or clean directory.
2. Install dependencies.
3. Configure dev/test DBs without committing secrets.
4. Run `alembic upgrade head` on blank databases.
5. Run the complete test suite.
6. Start the API.
7. Create/list/get/patch/delete using `curl` or `/docs`.
8. Restart and verify persistence.
9. Review Git diff for credentials, accidental generated files, and fat routes.
10. Finish architecture decision notes and endpoint worksheets.

Record a written or spoken explanation of:

- full request lifecycle;
- all layer boundaries;
- schemas versus ORM versus table versus migration;
- Session and transaction lifecycle;
- all Notes method/status/error choices;
- test levels and database isolation;
- how you would diagnose `422`, `404`, and `500`;
- why auth/integrations/agents are deliberately later.

Final completion condition: another developer can follow your README, migrate a blank PostgreSQL database, run the API, exercise all Notes operations, and run a green test suite—and you can explain why the system is built this way without reading the snippets.

## What comes next

Take a short retrospective before Phase 2:

- Which concept can you explain clearly?
- Which line still feels magical?
- Which failure took longest and what evidence solved it?
- Which contract choice would you now change, and would that be breaking?
- What repeated pattern, if any, has actually earned an abstraction?

Then start authentication with a new endpoint worksheet and a failing two-user isolation test. Do not begin Gmail or an agent loop merely because the Notes happy path works.
