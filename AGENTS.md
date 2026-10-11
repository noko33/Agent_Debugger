# Development instructions

## Scope and architecture

Agent Debugger is an open-source observability and debugging infrastructure project for
LLM agents, not a general-purpose chatbot. Use a modular monolith: one backend service,
one frontend application, and PostgreSQL. Read `docs/DEVELOPMENT_PLAN.md` before changing
scope. Implement only the phase authorized by the user. Phase 1 is the current scope;
stop for review before Phase 2. Never infer authorization to push, publish, or deploy.

## Repository responsibilities

- `backend/app/main.py`: application factory, middleware, and router registration.
- `backend/app/api/`: HTTP routes; keep business logic out of handlers.
- `backend/app/core/`: typed settings and shared infrastructure configuration.
- `backend/app/models/`: SQLAlchemy 2 project/trace/span/event persistence models.
- `backend/app/schemas/`: Pydantic v2 request and response contracts.
- `backend/app/services/`: ingestion, idempotency, and parent graph rules.
- `backend/app/repositories/`: SQLAlchemy queries and transaction-scoped row locks.
- `backend/alembic/`: versioned, self-contained PostgreSQL migrations.
- `backend/tests/`: backend unit and API tests.
- `frontend/app/`: Next.js App Router pages, layouts, and global styles.
- `frontend/components/`: reusable UI; `hooks/`: client lifecycle and state;
  `lib/`: API clients and shared utilities.
- `sdk/`: reserved for Phase 2 Python SDK and instrumentation.
- `docs/`: design, roadmap, and verification records; `examples/`: later integrations.

## Conventions and development rules

- Python 3.11+, FastAPI, Pydantic v2, uv, Ruff, and pytest. Annotate public functions.
- Use strict TypeScript, React function components, App Router, and Tailwind CSS.
- Prefer explicit dependencies and small modules. Do not create speculative abstractions.
- Settings belong in Pydantic Settings; browser configuration uses public Next.js env vars.
- Keep route contracts separate from persistence models and service implementations.
- Add comments for non-obvious decisions. Preserve existing useful documentation.
- Update the affected lockfile when changing dependencies. Use `uv sync --frozen` and `npm ci`.
- SQLAlchemy 2, migrations, and PostgreSQL JSONB are implemented in Phase 1.
  OpenTelemetry/OpenInference instrumentation, React Flow, and DeepSeek are planned;
  add them only in their authorized phases.
- Do not introduce Redis, Kafka, Kubernetes, microservices, agent frameworks, or
  additional external services without an explicit requirement.

## Testing and verification

From `backend/`, run `uv run ruff check .`, `uv run ruff format --check .`, and
`uv run python -m pytest`. From `frontend/`, run `npm run typecheck` and `npm run build`.
Validate `docker compose --env-file .env.example config --quiet` from the root.
For infrastructure work, start services when the environment permits, check `/health`,
check the landing page and its browser connection status, and execute a PostgreSQL query.
Add meaningful tests for changed behavior, including configuration and failure cases.
Do not require a live database for Phase 0 unit tests. Do not claim a check passed unless
it actually ran successfully; record environmental blockers and distinguish static
validation from runtime verification. CI configuration is not evidence of a successful CI run.
Phase 1 database-free tests use a repository test double; they are not PostgreSQL tests.
`tests/postgres/` runs only with an explicit `TEST_DATABASE_URL`, must use PostgreSQL,
and creates/removes its own generated schema in a disposable test database. A configured
but unreachable database must fail rather than silently skip. Run `alembic upgrade head`
before using ingestion APIs. Never change historical migration behavior by importing
current model metadata into a migration version. See `docs/TRACE_API.md` for contracts.

## Security

- Never commit `.env`, credentials, API keys, tokens, or database contents. Examples
  contain placeholders only. Never put secrets in `NEXT_PUBLIC_*` variables.
- Bind local Docker ports to loopback. Keep CORS origins explicit; no wildcard defaults.
- Never log database DSNs, authorization headers, prompts containing secrets, or keys.
- Treat future traces and tool outputs as untrusted input. Validate at API boundaries;
  plan redaction, payload limits, and retention before exposing ingestion externally.
- Do not send trace content to an LLM provider without an explicit product policy.
- Docker configuration is for local development; authentication, TLS, and production
  deployment hardening are outside Phase 0.

## Future Codex sessions

1. Inspect git status, this file, and the development plan before editing.
2. Preserve unrelated work. Confirm scope from the user's latest instructions.
3. Implement and verify the authorized phase without pulling future features forward.
4. Update status and verification documentation when behavior changes.
5. Report files changed, run commands, actual check results, and unresolved limitations.
6. Stop at the agreed review boundary. Do not push to GitHub automatically.
