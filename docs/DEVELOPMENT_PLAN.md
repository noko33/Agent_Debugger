# Agent Debugger development plan

## Principles and current scope

Build a modular monolith with a separate Next.js frontend and PostgreSQL persistence.
Each phase requires review before starting the next. The roadmap describes planned
functionality, not existing capabilities. Phase 0 and Phase 1 code are implemented.
Phase 0 and Phase 1 local runtime acceptance passed on 2026-10-12 after Docker/WSL
recovery, including all 32 PostgreSQL integration tests. Phase 2A is authorized and
implemented and locally verified. Phase 2B explicit Tool/LLM decorators are verified.
Phase 3A adds the Trace Explorer and read-only execution tree; further scope requires review.
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

## Phase 2 — Python SDK and OpenTelemetry instrumentation (Phase 2A/2B implemented)

### Phase 2A — Minimal explicit tracing SDK

**Objectives:** instrument a Python Agent explicitly, reuse OpenTelemetry context, and
export completed records into the existing Phase 1 API without changing its contracts.

**Deliverables:** installable `agent-debugger-sdk` package; `AgentDebugger.trace()` and
`span()` context managers yielding OTel spans; parent relationships, timestamps, attributes,
status and exceptions; bounded synchronous API exporter with finite retries/redaction;
example Agent; unit tests and real HTTP/PostgreSQL end-to-end tests; installation guide.

**Acceptance criteria:** example trace and nested spans persist and can be retrieved;
original Agent exceptions remain observable; API outages do not replace application
results/exceptions; retries preserve existing idempotency; OTel contexts isolate async
tasks and support standard thread attach/detach; SDK tests, backend regression, lint,
and package builds pass. Stop for review before additional instrumentation.

**Decisions and limits:** each `trace()` creates a fresh root with a private OTel provider.
Ended spans are buffered until the root ends because Trace records are immutable. Send
the complete Trace followed by one bounded span batch; delivery across these two requests
is not atomic. There is no persistent queue or live update API. Child tasks must finish
before the root; overflow drops the buffered trace. See `../sdk/README.md` for usage,
context propagation, payload limits, redaction and failure behavior.

### Phase 2B — Explicit LLM and Tool tracing (implemented; locally verified)

**Objectives:** make sync/async function instrumentation convenient while preserving
OTel context and the existing ingestion contracts. No framework interception.

**Deliverables:** `trace_tool` and `trace_llm` decorators; opt-in argument/result shape
metadata; caller/adapter-reported token counts; provider/model labels; type-only exception
events; cancellation lifecycle fix; deterministic example with optional failure/recovery;
unit tests and real HTTP/PostgreSQL round-trip tests.

**Acceptance criteria:** sync/async calls preserve results and original exceptions;
concurrent tasks do not leak parents; default payloads omit prompts, values and exception
messages; unknown token counts remain absent; ingestion persists hierarchy and metadata;
SDK/backend regression and lint pass. See `VERIFICATION.md` for actual results.

**Limits:** explicit decorators only, no streaming/generator instrumentation. Export
remains bounded and synchronous at root completion. No paid provider is required.
Manual context managers retain detailed exceptions for compatibility; use
`record_exception_details=False` for sensitive workloads. Stop for user review here.

### Remaining Phase 2 — Planned, not authorized by Phase 2B

Automatic framework instrumentation, remote trace continuation, OTLP/OpenInference
adapters, broader resource/scope mapping, durable/background transport, and additional
runtime controls require a separate design and authorization. The original full-phase
objectives below remain the roadmap, not a claim of completed functionality.

**Objectives:** capture execution context without coupling agent applications to storage.

**Deliverables:** installable Python SDK; explicit span/trace helpers; OpenTelemetry
export path; OpenInference attribute mapping; configuration, bounded retries/timeouts,
redaction hooks, and shutdown/flush behavior; a small instrumented example.

**Acceptance criteria:** an example emits a correctly parented trace to Phase 1 APIs;
timestamps, errors, and tool boundaries survive round-trip; exporter outages do not crash
the host application; SDK and exporter tests pass; sensitive fields can be redacted.

## Phase 3 — Next.js dashboard and execution visualization (Phase 3A implemented)

### Phase 3A — Trace Explorer and interactive execution tree

**Objectives:** browse real stored executions, navigate parent-child relationships, and
inspect recorded span/event metadata using existing read APIs.

**Deliverables:** paginated trace list; abortable loading/error/retry states; complete
paged span loading up to a documented 5,000-span bound; React Flow nodes for Agent/LLM/
Tool/Runtime with status/exception indicators; pan/zoom/fit controls; accessible span
list and parent navigation; safe text-only detail panel; frontend unit/component and
real graph regression tests; actual browser verification against the existing backend.

**Acceptance criteria:** the Phase 2B example renders seven spans and six recorded edges;
Lookup parents Read local catalog; failure and recovery events remain inspectable;
unknown timing/usage remains explicit; missing parents/cycles produce warnings without
hiding spans; trace/API states and span selection are tested; TypeScript, production
build, frontend tests and backend regression pass. Stop for user review after Phase 3A.

**Decisions and limits:** reuse all Phase 1 APIs and database/SDK contracts. Graph position
represents hierarchy, using stable start-time/ID ordering, not elapsed-time spacing.
Use API-derived durations to retain microsecond precision. Attributes/events are escaped
React text. No streaming, filtering dashboard expansion, diagnosis or repair is included.
Large graph virtualization/performance tuning and further dashboard scope remain planned.

### Full Phase 3 roadmap

The original objectives below include the Phase 3A subset already delivered. Expanded
filtering, dashboard capabilities and large-graph performance work require further review.

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
