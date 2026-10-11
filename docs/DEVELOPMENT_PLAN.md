# Agent Debugger development plan

## Principles and current scope

Build a modular monolith with a separate Next.js frontend and PostgreSQL persistence.
Each phase requires review before starting the next. The roadmap describes planned
functionality, not existing capabilities. Phase 0 and Phase 1 code are implemented.
Phase 0 and Phase 1 local runtime acceptance passed on 2026-10-12 after Docker/WSL
recovery, including all 32 PostgreSQL integration tests. Phase 2 requires user review.
Verification results and environment limitations are recorded in `VERIFICATION.md`.

## Phase 0 — Project initialization

**Objectives:** establish reproducible local development, boundaries, and quality checks.

**Deliverables:** typed FastAPI configuration and `/health`; Next.js/TypeScript/Tailwind
landing page with API connection status; PostgreSQL Compose service with persistent
storage; development Dockerfiles; environment examples; uv/npm lockfiles; Ruff, pytest,
TypeScript/build checks; basic GitHub Actions; architecture and roadmap documentation.

**Acceptance criteria:** FastAPI starts and `/health` returns HTTP 200 with its documented
JSON contract; Next.js starts; PostgreSQL accepts a query; the browser displays backend
connection status; backend tests, lint, formatting, frontend type checks and build pass;
Compose validates and all services can start together on Docker Desktop with WSL2.
Report unavailable checks honestly; runtime criteria remain unverified until executed.
No trace models, ingestion, instrumentation, SDK logic, diagnosis, or LLM integration.

## Phase 1 — Trace schema, database, and ingestion APIs (implemented; locally verified)

**Objectives:** establish a versioned execution data model and reliable persistence.

**Deliverables:** SQLAlchemy 2 models and Alembic migrations; PostgreSQL JSONB metadata;
Pydantic contracts for traces/spans, parent relationships, timestamps, status, agent/tool
context and errors; bounded ingestion, trace listing and retrieval APIs; API documentation.
Define transaction behavior, duplicate handling, redaction, and pagination explicitly.

**Acceptance criteria:** migrations apply to a clean PostgreSQL database; a validated
execution with nested spans can be persisted and retrieved with stable identifiers;
invalid payloads and duplicate semantics are tested; pagination works; integration tests
verify real database behavior; no SDK or diagnosis is required yet.

**Implementation decisions:** use a Project → Trace → Span → Event hierarchy with
PostgreSQL JSONB attributes, non-zero OpenTelemetry-sized hex IDs, UTC timestamps,
database-generated durations, and bounded requests/pages. Spans use `(trace_id, span_id)`
as their key. Exact retries are no-ops; conflicting retries return 409. A trace-level
row lock serializes graph validation and inserts. Missing parents are allowed;
self-parenting and cycles are rejected. Batches are transactional. See `TRACE_API.md`
for API examples, instrumentation mapping, limits and tradeoffs. PostgreSQL-only tests
cover real constraints, migrations, rollback and concurrency when a test database exists.

## Phase 2 — Python SDK and OpenTelemetry instrumentation (planned)

**Objectives:** capture execution context without coupling agent applications to storage.

**Deliverables:** installable Python SDK; explicit span/trace helpers; OpenTelemetry
export path; OpenInference attribute mapping; configuration, bounded retries/timeouts,
redaction hooks, and shutdown/flush behavior; a small instrumented example.

**Acceptance criteria:** an example emits a correctly parented trace to Phase 1 APIs;
timestamps, errors, and tool boundaries survive round-trip; exporter outages do not crash
the host application; SDK and exporter tests pass; sensitive fields can be redacted.

## Phase 3 — Next.js dashboard and execution visualization (planned)

**Objectives:** let developers find executions and inspect their structure and context.

**Deliverables:** paginated trace list, trace detail view, React Flow execution graph,
span details, latency/status indicators, and basic filtering; accessible loading,
empty, and failure states. Use real API data rather than presenting mock data as live.

**Acceptance criteria:** ingested traces can be located and inspected; parent/child
relationships match stored data; API failures are recoverable; representative large
traces remain usable; component and end-to-end tests cover core navigation.

## Phase 4 — Rule-based failure detection (planned)

**Objectives:** identify deterministic, explainable execution failure patterns.

**Deliverables:** versioned detection rules; initial taxonomy for tool errors, loops,
timeouts, and budget overruns where trace evidence supports them; structured findings
with evidence span IDs, severity, and rule IDs; UI findings display and rule fixtures.

**Acceptance criteria:** known fixtures produce expected findings; negative fixtures
avoid false positives; every finding links to trace evidence and its rule version;
results are deterministic and queryable; unsupported claims are not inferred from
missing instrumentation.

## Phase 5 — LLM-assisted diagnosis and fix recommendations (planned)

**Objectives:** supplement rule evidence with reviewable diagnoses and recommendations.

**Deliverables:** server-side DeepSeek provider adapter; Pydantic-validated structured
outputs using the provider's supported capabilities; bounded prompt construction,
redaction, timeout/retry and cost controls; prompt/policy/tool/runtime suggestions;
clear separation of observed facts from model hypotheses. No automatic agent changes.

**Acceptance criteria:** mocked provider tests cover valid, malformed, refused, and
unavailable responses; results reference evidence and indicate uncertainty; credentials
stay server-side; sensitive traces follow an explicit consent/redaction policy;
diagnoses are reviewable and no fixes are applied without user action.

## Phase 6 — Integration testing, CI/CD, and documentation (planned)

**Objectives:** validate the full workflow and prepare a documented release process.

**Deliverables:** end-to-end SDK → ingestion → persistence → visualization → diagnosis
tests; security and performance baselines; expanded CI, container build and optional
release workflows; API/SDK guides, troubleshooting, contribution and deployment docs.
Basic CI starts in Phase 0; deployment automation requires separate approval.

**Acceptance criteria:** documented example runs from a clean environment; migration,
integration and end-to-end checks pass in CI; dependency and secret checks are in place;
known limits are documented; release artifacts are reproducible. Publishing and
deployment occur only when explicitly authorized.
