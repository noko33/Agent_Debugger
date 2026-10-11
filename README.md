# Agent Debugger

An open-source, trace-based debugging and failure analysis platform for LLM agents.
The goal is to help developers understand multi-step agent executions, identify failure
patterns, and improve prompts, tool usage, policies, and runtime controls. Agent Debugger
is observability and debugging infrastructure, not a general-purpose chatbot.

Agent failures often span several model and tool interactions. A trace provides the
execution context needed to investigate what happened and support evidence-based fixes.

## Current implementation status

**Phase 0:** FastAPI `/health`, typed environment settings and local CORS; a minimal
Next.js landing page with browser-side backend status and retry; PostgreSQL development
configuration; Docker Compose; backend tests; lint/type/build checks; basic CI.
**Phase 1 code:** validated Project/Trace/Span/Event contracts, SQLAlchemy models,
Alembic migration, ingestion/retrieval APIs, pagination, duplicate and graph handling,
and separate PostgreSQL integration tests. Phase 0 and Phase 1 local runtime acceptance
passed on 2026-10-12: all three Docker services, migrations, live APIs, browser connection,
and 89 backend tests including 32 PostgreSQL integration tests were verified.
See [verification results](docs/VERIFICATION.md) for checks actually run and limitations.

**Planned features:** Python SDK, OpenTelemetry/OpenInference
instrumentation, execution graphs, failure classification, LLM-assisted diagnosis,
and fix recommendations. None of these features is implemented yet.

## Proposed technology stack

| Area | Implemented/configured | Planned additions |
| --- | --- | --- |
| Backend | Python 3.11+, FastAPI, Pydantic v2, Pydantic Settings, SQLAlchemy 2, Alembic, psycopg | — |
| Frontend | Next.js App Router, React, strict TypeScript, Tailwind CSS | React Flow |
| Database | PostgreSQL 17, persistent volume, trace schema and JSONB attributes; runtime verified | — |
| Observability | — | Python SDK, OpenTelemetry, OpenInference |
| Diagnosis | — | Rule-based detection, DeepSeek API with validated structured outputs |
| Tooling | uv, Ruff, pytest, npm, GitHub Actions, Docker Compose | Expanded integration/release checks |

Dependencies are locked in `backend/uv.lock` and `frontend/package-lock.json`.
No Redis, Kafka, Kubernetes, or microservices are required.

## Architecture and repository

```text
Browser → Next.js frontend → FastAPI modular monolith → PostgreSQL
                                ↑
                    Python SDK / instrumentation (planned)
```

The browser calls `GET /health` directly on FastAPI. Phase 1 adds database-backed
`/api/v1` routes. Compose checks PostgreSQL, applies migrations, and then starts the API.
Native runs apply migrations explicitly. The frontend remains the Phase 0 landing page.
`/health` reports API liveness, not database or platform readiness.

```text
backend/app/
  api/          HTTP routes
  core/         typed configuration
  models/       SQLAlchemy project/trace/span/event models
  schemas/      API response contracts
  services/     ingestion and graph policies
  repositories/ database queries and row locks
  main.py       application factory
backend/alembic/ versioned PostgreSQL migrations
backend/tests/  database-free tests and opt-in PostgreSQL integration tests
frontend/
  app/          pages and layout
  components/   connection status UI
  hooks/        connection lifecycle
  lib/          typed API client
sdk/            reserved for Phase 2
docs/           development plan and verification
examples/       reserved for later integration examples
.github/workflows/ci.yml
```

## Run on Windows with Docker Desktop

Install Docker Desktop with the WSL2 Linux container backend enabled and start it.
Run these commands in PowerShell from the repository root:

```powershell
Copy-Item .env.example .env
notepad .env
# Replace POSTGRES_PASSWORD with your own local password, then save.
docker compose config --quiet
docker compose up --build -d --wait
Invoke-RestMethod http://localhost:8000/health
Start-Process http://localhost:3000
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1;"'
```

Expected API response: `{"status":"ok","service":"agent-debugger-api"}`.
The page should show **Backend connected**. API docs: <http://localhost:8000/docs>.
The public URL must be reachable by the browser; use `localhost`, not Docker service
names. If changing ports, update `NEXT_PUBLIC_API_BASE_URL` and `CORS_ORIGINS` too.
Ports bind to loopback for local development. This is not a production deployment.

```powershell
docker compose logs -f
docker compose down
```

Database data remains in `postgres_data` across container restarts. `docker compose down -v`
**deletes local database data**. Changing PostgreSQL credentials in `.env` does not change
credentials already initialized in an existing volume. Source files are mounted for
reload; rebuild images after dependency changes. Frontend Compose uses Webpack polling
to support Docker Desktop bind mounts.

## Native development and checks

Install Python 3.11+, uv, and Node.js 22+; Node 22 is used in Docker and CI.
See the official [Next.js setup guide](https://nextjs.org/docs/app/getting-started/installation)
and [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
Start PostgreSQL with `docker compose up -d postgres` if needed; Phase 0 health tests
and database-free tests can run without a database. Ingestion/retrieval requires a
configured, migrated PostgreSQL database.

Backend, in one PowerShell terminal:

```powershell
cd backend
uv sync --frozen
Copy-Item .env.example .env
notepad .env
# Match the PostgreSQL password in the root .env used by Compose.
uv run python -m alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend, in another terminal from the repository root:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm.cmd ci
npm.cmd run typecheck
npm.cmd run build
npm.cmd run dev
```

Using `npm.cmd` avoids PowerShell script execution-policy restrictions. `npm.cmd run start`
serves the production build after `build`. `NEXT_PUBLIC_*` values are embedded at build
time for production, so rebuild after changing the backend URL.

Backend settings read environment variables and an optional `.env` in the backend working
directory. Defaults allow `http://localhost:3000` for CORS. To change it, set
`CORS_ORIGINS` to a JSON array. Optional `DATABASE_URL` accepts a PostgreSQL DSN and
overrides the `POSTGRES_*` component settings in native runs. Without either a DSN or
`POSTGRES_PASSWORD`, health works and data routes return HTTP 503. Use host `postgres`
inside Compose and `localhost` for native runs.
URL-encode reserved characters in credentials. Do not commit `.env` or any real secrets.
Compose reads root `.env`; it passes only explicitly configured variables to services.
Compose passes PostgreSQL component settings to the backend, so passwords do not need
manual DSN encoding. It applies `alembic upgrade head` before starting this single local
API service. See [the Phase 1 API guide](docs/TRACE_API.md) for JSON examples, duplicate
semantics, out-of-order ingestion, limits, and PostgreSQL integration-test commands.

## Roadmap

1. **Phase 0:** initialization and local infrastructure (implemented; Docker verification pending).
2. **Phase 1:** trace schema, migrations, ingestion/retrieval APIs (current scope; database verification pending).
3. **Phase 2:** Python SDK and OpenTelemetry/OpenInference instrumentation.
4. **Phase 3:** dashboard and execution visualization.
5. **Phase 4:** deterministic rule-based failure detection.
6. **Phase 5:** LLM-assisted diagnosis and fix recommendations.
7. **Phase 6:** full integration tests, expanded CI/CD, release documentation.

See [the development plan](docs/DEVELOPMENT_PLAN.md) for objectives, deliverables and
acceptance criteria, and [AGENTS.md](AGENTS.md) for development rules. Phase 2 requires
review and authorization before implementation.
