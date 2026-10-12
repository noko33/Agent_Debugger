# Development instructions

## Scope and architecture

Agent Debugger is an open-source observability and debugging infrastructure project for
LLM agents, not a general-purpose chatbot. Use a modular monolith: one backend service,
one frontend application, and PostgreSQL. Read `docs/DEVELOPMENT_PLAN.md` before changing
scope. Implement only the phase authorized by the user. Features through Phase 5C2 are
implemented. The latest request authorizes final QA, integration/acceptance verification,
documentation, and confirmed acceptance-blocking defect fixes only. No new product features.
Stop after final QA for review. Never apply a suggested edit or execute model-generated code.
See the final QA checkpoint in `docs/VERIFICATION.md` for executed and deferred checks.
Never infer authorization to push, publish, or deploy.

## Repository responsibilities

- `backend/app/main.py`: application factory, middleware, and router registration.
- `backend/app/api/`: HTTP routes; keep business logic out of handlers.
- `backend/app/core/`: typed settings and shared infrastructure configuration.
- `backend/app/models/`: SQLAlchemy 2 project/trace/span/event persistence models.
- `backend/app/schemas/`: Pydantic v2 request and response contracts.
- `backend/app/services/`: ingestion, idempotency, and parent graph rules.
- `backend/app/services/fix_suggestions.py` and `fix_templates.py`: versioned, stateless
  review-only proposals; no executed edits, fabricated causes or model-generated commands.
- `backend/app/services/diagnostics/`: pure deterministic rules, evidence and recorded
  outcome classification; `diagnostic_service.py` coordinates snapshot analysis.
- `backend/app/services/replay.py`: immutable historical snapshots and deterministic
  analysis replay, never proof of repair effectiveness.
- `backend/app/services/evaluation.py` and `evaluation_runner.py`: explicit correctness
  criteria, coverage-aware metrics, comparisons and registered trusted local functions.
  The runner is not a security sandbox; never accept source code/import paths from data.
- `backend/app/services/fix_validation.py`: source suggestion/reference validation,
  developer-approved distinct registered implementations and frozen same-dataset execution.
- `backend/app/fix_validation_demo.py`: explicit mock SDK/local-file demonstration only;
  not imported by production routes and requires the existing optional editable SDK.
- `backend/app/repositories/diagnostics.py`: bounded consistent read snapshots, sharing
  the trace lock convention with ingestion; never persist diagnostic findings.
- `backend/app/repositories/`: SQLAlchemy queries and transaction-scoped row locks.
- `backend/alembic/`: versioned, self-contained PostgreSQL migrations.
- `backend/tests/`: backend unit and API tests.
- `frontend/app/`: Next.js App Router pages, layouts, and global styles.
- `frontend/components/`: Trace Explorer, React Flow graph, timeline, performance summary,
  shared filters and safe span/event detail UI;
  `hooks/`: abortable client requests and state; `lib/`: API contracts and tree construction.
- `frontend/tests/`: Vitest/jsdom model, API, component and graph selection regression tests.
- `sdk/src/agent_debugger/`: explicit OTel context-manager tracing and API exporter.
- `sdk/tests/`: database-free SDK tests; SDK HTTP/PostgreSQL tests reuse backend fixtures.
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
  Phase 2A/2B use OpenTelemetry API/SDK for explicit tracing and Tool/LLM decorators.
  Phase 3A uses React Flow for the read-only execution tree. Phase 3B adds a timestamp-based
  timeline and statistics without dependencies. Preserve microsecond timestamps, shared
  selection and ancestor context when filtering. Never sum overlapping spans as elapsed
  execution time or estimate missing tokens. Automatic instrumentation,
  OpenInference/OTLP adapters remain planned; add them only in their authorized phases.
  Phase 5A adds optional compatible-provider hypotheses with external processing disabled
  by default; see `docs/AI_DIAGNOSIS.md`. Do not replace the host application's global
  OTel provider.
- Do not introduce Redis, Kafka, Kubernetes, microservices, agent frameworks, or
  additional external services without an explicit requirement.
- Phase 4A findings must distinguish observed failures, heuristic warnings and data
  quality. Require explicit retry metadata/fingerprints for loop warnings and explicit
  root recovery evidence for recovered outcomes. Do not infer semantic success, prompts,
  arguments, root causes or confidence percentages. Phase 4B must display backend outcome
  classifications and reuse shared graph/timeline/detail selection; never infer recovery
  from a later successful operation. Keep requests abortable and reject stale results.

## Testing and verification

From `backend/`, run `uv run ruff check .`, `uv run ruff format --check .`, and
`uv run --with-editable ../sdk python -m pytest`. From `sdk/`, run `uv run ruff check .`,
`uv run ruff format --check .`, `uv run python -m pytest`, and `uv build`.
From `frontend/`, run `npm test`, `npm run typecheck` and `npm run build` when frontend
code changes. Component tests use a graph double except the real React Flow selection
regression test, which uses DOM measurement shims. Verify actual graph interactions in
a browser and report them separately from jsdom/build results.
Validate `docker compose --env-file .env.example config --quiet` from the root.
For infrastructure work, start services when the environment permits, check `/health`,
check the landing page and its browser connection status, and execute a PostgreSQL query.
Add meaningful tests for changed behavior, including configuration and failure cases.
For Phase 5C1/5C2, the user requests focused automated checks only. Defer full PostgreSQL/
HTTP/browser/project-wide acceptance until after Phase 5C2 and label it unexecuted.
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
- Phase 5A external analysis is disabled by default and requires per-request consent.
  Use the documented 5a.1 allowlist, bounded input/output and validated evidence citations.
  Keep deterministic findings available on provider failure. No paid live tests without
  separate approval; mocked tests do not establish diagnostic accuracy.
- Docker configuration is for local development; authentication, TLS, and production
  deployment hardening are outside Phase 0.

## Future Codex sessions

1. Inspect git status, this file, and the development plan before editing.
2. Preserve unrelated work. Confirm scope from the user's latest instructions.
3. Implement and verify the authorized phase without pulling future features forward.
4. Update status and verification documentation when behavior changes.
5. Report files changed, run commands, actual check results, and unresolved limitations.
6. Stop at the agreed review boundary. Do not push to GitHub automatically.
