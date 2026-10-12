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

**Phase 2A:** a minimal installable Python SDK with explicit OpenTelemetry Trace/Span
context managers, bounded API export, safe failures, redaction, and a local Agent example.
**Phase 2B:** sync/async Tool and LLM decorators, safe opt-in shape metadata, explicit
token usage adapters, cancellation handling, and a no-provider example with recovery.
See [SDK installation and usage](sdk/README.md). Further work requires review.

**Phase 3A:** a real-data Trace Explorer at `/`, paginated trace browsing, interactive
React Flow execution tree, exception/status indicators, and safe span/event details.
See [Explorer usage and limits](docs/TRACE_EXPLORER.md).

**Phase 3B:** interactive timestamp-based waterfall with shared span selection, consistent
kind/status/name filters retaining ancestor context, and real-data performance statistics
(wall-clock duration, counts, recorded tokens with coverage, and slowest spans).

**Phase 4A:** deterministic on-demand diagnostics for explicit Tool/LLM errors, repeated
tool failures, evidence-gated possible retries, slow operations and trace data quality.
Recorded recovery/outcome is separate from child failures. See
[rule conditions, API and limits](docs/DIAGNOSTICS.md).

**Phase 4B:** interactive diagnostics panel with recorded recovery/outcome, category and
severity filters, explainable evidence/parameters and shared multi-span navigation across
the graph, timeline and details. All findings come from the existing backend API.

**Phase 5A:** optional evidence-grounded LLM hypotheses through an explicit API,
allowlisted context, consent, bounded compatible-provider transport and citation validation.
Disabled by default. Local runtime acceptance passed on 2026-10-13: 296 backend tests
including 96 PostgreSQL checks, real HTTP/API verification and 59 SDK tests passed.
No paid model was called. See
[AI diagnosis policy, API and limitations](docs/AI_DIAGNOSIS.md).

**Phase 5B implementation:** explicit evidence-grounded repair proposals in the Diagnostics
Panel, with conservative templates, optional validated hypothesis links, risks, missing
evidence and manual validation plans. No changes are applied. Automated and real HTTP/
PostgreSQL checks pass; actual request/rendering/Span-navigation browser acceptance passed
during Final QA. See [repair suggestions](docs/FIX_SUGGESTIONS.md).

**Phase 5C1:** immutable deterministic analysis replay, explicit evaluation datasets and
validators, coverage-aware metrics, baseline/candidate comparison and a trusted local
Python runner. Focused checks and the complete Final QA regression pass.
See [evaluation workflow](docs/EVALUATION.md).

**Phase 5C2:** developer-approved controlled baseline/candidate execution linked to an
existing suggestion, with frozen datasets, structured provenance/results and trade-offs.
A deterministic SDK/local-file catalog fallback example is available, with additional
real HTTP/PostgreSQL execution verification in Final QA; no automatic edits or execution HTTP API.

**Final QA (2026-10-13):** 432 distinct backend tests (121 PostgreSQL), 59 SDK tests and
63 frontend tests verified, with no final failures/skips; lint/type/build checks pass.
Existing Docker services, real HTTP fix validation and core browser interactions passed.
Some browser edge scenarios, clean-checkout CI and release hardening remain unverified.
See [actual results and release limitations](docs/VERIFICATION.md) and
[UI usability recommendations](docs/UI_USABILITY_REVIEW.md). No publication/push was performed.

**Planned features:** automatic instrumentation, OTLP/OpenInference adapters,
expanded dashboard features and additional release/production hardening.

## Proposed technology stack

| Area | Implemented/configured | Planned additions |
| --- | --- | --- |
| Backend | Python 3.11+, FastAPI, Pydantic v2, Pydantic Settings, SQLAlchemy 2, Alembic, psycopg | — |
| Frontend | Next.js App Router, React, strict TypeScript, Tailwind CSS, React Flow | Expanded dashboard |
| Database | PostgreSQL 17, persistent volume, trace schema and JSONB attributes; runtime verified | — |
| Observability | Explicit Python tracing SDK using OpenTelemetry; ingestion API exporter | Automatic instrumentation, OTLP/OpenInference adapters |
| Diagnosis | Deterministic rules, evidence navigation, optional hypotheses, review-only proposals, replay/evaluation and controlled local fix validation | Final acceptance/hardening |
| Tooling | uv, Ruff, pytest, npm, GitHub Actions, Docker Compose | Expanded integration/release checks |

Dependencies are locked in `backend/uv.lock`, `sdk/uv.lock`, and `frontend/package-lock.json`.
No Redis, Kafka, Kubernetes, or microservices are required.

## Architecture and repository

```text
Browser → Next.js frontend → FastAPI modular monolith → PostgreSQL
                                ↑
                    Python SDK (explicit Phase 2A/2B tracing)
```

The browser calls the existing FastAPI `/api/v1` trace list/detail/span APIs directly.
Compose checks PostgreSQL, applies migrations, and then starts the API. Native runs
apply migrations explicitly. The frontend displays stored traces, execution trees and timelines.
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
  components/   Explorer, graph, timeline, statistics and span details
  hooks/        abortable Explorer requests and connection lifecycle
  lib/          typed API client, span tree, timing, aggregation and filtering
sdk/            Python tracing package, exporter, and unit tests
docs/           development plan and verification
examples/       minimal Agent with explicit tracing
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

Install Python 3.11+, uv, and Node.js 22.22.2+ on the 22.x line, or 24.15.0+ on
the 24.x line (Node 26+ is also supported). Node 22 is used in Docker and CI.
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
uv run --with-editable ../sdk python -m pytest
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend, in another terminal from the repository root:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm.cmd ci
npm.cmd test
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

1. **Phase 0:** initialization and local infrastructure (locally verified).
2. **Phase 1:** trace schema, migrations, ingestion/retrieval APIs (locally verified).
3. **Phase 2A/2B:** explicit tracing SDK and Tool/LLM decorators (locally verified); automatic framework instrumentation remains planned.
4. **Phase 3A:** Trace Explorer and interactive execution tree (locally verified); further dashboard work remains planned.
5. **Phase 4A/4B:** deterministic rules and interactive evidence navigation (implemented).
6. **Phase 5A–5C2:** optional hypotheses, review-only suggestions and controlled local evaluation (implemented).
7. **Final QA:** local regressions, real HTTP/PostgreSQL and core browser acceptance completed with documented deferrals.
8. **Phase 6 remainder:** expanded CI/CD, clean-checkout release checks and production hardening (planned).

See [the development plan](docs/DEVELOPMENT_PLAN.md) for objectives, deliverables and
acceptance criteria, and [AGENTS.md](AGENTS.md) for development rules. Stop after Final QA;
further implementation requires review and authorization.
