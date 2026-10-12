# Phase 5C1/5C2: analysis replay and controlled fix validation

This phase provides Python service interfaces, without a new HTTP API, frontend,
database table, dependency or automatic repair workflow. The latest request authorizes
Phase 5C2 controlled execution of developer-supplied candidates. Comprehensive
PostgreSQL/HTTP/browser acceptance remains deferred to a separate final acceptance task.

## Historical replay versus new execution

`app.services.replay.load_snapshot(repository, trace_id, parameters, project_id=None)`
uses the existing `DiagnosticRepository` contract. With `SQLDiagnosticRepository`, call
inside a transaction: its shared trace row lock follows ingestion's locking convention
and protects the complete span/event read. Serialize before closing the transaction.
It does not protect against direct SQL writers bypassing that convention.

`AnalysisSnapshot.capture(trace, spans, truncated=False)` validates the existing read
contracts, sorts spans for reproducibility, and stores canonical immutable JSON bytes.
Its SHA-256 digest identifies the captured content. No mutable ORM/Pydantic objects are
retained. Each replay decodes fresh objects. Capture rejects malformed inputs; importing
invalid offline bytes through `replay` returns `invalid` with a generic issue code.
Snapshots contain original attributes/events and can contain sensitive data. Do not log
or commit them; use the ignored `.evaluation-artifacts/` directory and a local retention
policy. Digests are content fingerprints, not anonymization or authenticity proofs.

`replay(snapshot, parameters, original=None)` runs only the existing pure deterministic
rules. Effective thresholds and rule/schema versions remain recorded. No SDK exports,
Agent execution, model calls, prompt changes or database writes occur. A missing trace
returns `missing`; malformed data/limits return `invalid`; valid records with data-quality
findings return `incomplete`. Explicitly truncated offline snapshots force unknown
execution outcome and `partial_snapshot` coverage. The database loader rejects oversized
reads rather than silently truncating them. Stored completeness never guarantees that
instrumentation/export captured every real operation.

Optional comparison requires matching trace/project and diagnostic rule/schema versions.
Finding identities hash rule ID plus referenced span IDs, matching the current engine's
one-finding-per-rule/span-group contract. Full content determines stable versus changed;
added/removed identities are separate. Missing evidence means an old referenced span or
event index/timestamp/exception signal is absent. Changing a threshold does not mean
evidence disappeared.
Missing evidence is reported with opaque evidence digests; inspect the original result
for details. Results record whether snapshot digests match. Different/partial snapshots
support descriptive comparison only. Original results must be trusted recorded artifacts;
this phase does not cryptographically verify their provenance.

```python
from sqlalchemy.orm import Session
from app.repositories.diagnostics import SQLDiagnosticRepository
from app.schemas.diagnostics import DiagnosticParameters
from app.services.replay import load_snapshot, replay

parameters = DiagnosticParameters(slow_operation_ms=1000)
# engine is the application's existing configured SQLAlchemy engine.
with Session(engine) as session, session.begin():
    snapshot = load_snapshot(SQLDiagnosticRepository(session), trace_id, parameters)
original = replay(snapshot, parameters)
repeated = replay(snapshot, parameters, original=original)
changed = replay(snapshot, DiagnosticParameters(slow_operation_ms=500), original=original)
```

**Historical replay cannot prove a proposed fix works.** It reinterprets old evidence;
it does not execute changed Agent behavior or produce a counterfactual execution.

## Dataset and correctness contracts

Models live in `backend/app/schemas/evaluation.py`:
`EvaluationDataset`, `EvaluationCase`, `EvaluationRun`, `EvaluationResult`, `MetricResult`,
and `BaselineCandidateComparison`, plus criterion/configuration/observation helpers.
All support Pydantic JSON validation/serialization and reject unexpected fields.
Case IDs must be unique and stable. Dataset versions identify intentional dataset changes.

```json
{
  "dataset_id": "arithmetic",
  "version": "1",
  "cases": [{
    "case_id": "add-2-3",
    "task": "Add two integers",
    "inputs": {"left": 2, "right": 3},
    "criterion": {"kind": "structured_values", "expected": {"answer": 5}}
  }]
}
```

- `exact_match`: type-sensitive canonical JSON equality, including whitespace/case in
  strings. Boolean true does not equal integer 1; numeric 1.0 and 1 serialize differently.
- `structured_values`: recursively require the expected object's keys/values; permit
  additional object keys. Lists and scalar leaves require exact equality.
- No criterion: outcome is `unknown`, even if the Agent root span is OK or recovered.
  `evaluate_case(..., label=True/False)` optionally accepts an explicit **observed**
  human/fixture success label for offline data when no validator exists. Expected success
  is never automatically used as an observed label for a new Agent run.

An omitted offline output also returns unknown unless an explicit observed label is
supplied. Explicitly passing JSON null is a recorded output and can be validated against
an expected null. Missing output is not automatically counted as incorrect.

Validators express only their declared criteria, not general semantic correctness.
Agent errors/timeouts/invalid outputs are execution failures even without a validator.
Task outcomes and diagnostic recorded execution outcomes remain separate fields.

## Trusted local execution

`TrustedEvaluationRunner.register(name, function)` accepts explicit importable top-level
Python functions controlled by the host developer. `run` accepts only a registered name;
dataset JSON cannot specify code, import paths, commands or patches. Sync and async
functions receive a validated `EvaluationCase` and return `AgentOutput(value=...,
trace_ids=[...])`. Use existing SDK tracing inside the function if desired; no global
OpenTelemetry provider is changed by the evaluation framework.

Each case runs in a fresh spawned process (Windows compatible), sequentially. Default
deadline is 5 seconds, including process startup; maximum is 300. A timed-out worker is
terminated, with bounded cleanup grace. Output is finite JSON, default maximum 64 KiB,
including the serialized envelope. Errors use safe codes without raw exception messages.
Execution duration includes startup and cleanup overhead, so it is not pure Agent latency.
Unexpected worker termination yields `worker_error`; oversized/malformed output yields
`invalid_output`. The runner records Python version, callable identity/source digest,
configuration, supplied provenance and case-content digests. Supply dependency revisions,
seeds, fixture versions and environment descriptions as additional provenance when needed.

This is **trusted local execution, not a security sandbox**. Functions retain filesystem
and network permissions, can emit their own logs, and may have external side effects.
Termination cannot undo those effects or guarantee cleanup of independently launched
resources. No memory/CPU quota, credential isolation or arbitrary-source isolation is
provided. Use only deterministic, side-effect-controlled mocks for automated development.

Optional `resolve_trace(trace_id, parameters)` runs in the parent process and can reuse
`load_snapshot`/`replay` after export. It must use a bounded database connection/query
timeout; the Agent deadline does not bound this host-supplied callback. A read failure
keeps the captured task outcome and marks trace observation unresolved. Phase 5C1 attaches
at most one observation per case. Multiple returned trace IDs are retained but marked
`multiple_traces`, with no invented merged timing/usage. No trace is automatically exported.

## Metrics and comparison

Every `MetricResult` includes `value`, `denominator`, `observed`, unit and an explanation.
Missing measurements are null, not zero. `aggregate(results)` reports:

| Metric | Meaning / coverage |
| --- | --- |
| task_count, failed_task_count, unknown_task_count | Evaluated case counts |
| success_rate | Successful / known outcomes; unknown excluded; known_task_count shows known / all coverage |
| execution_duration_ms | Sum of measured per-case runner wall times; excludes trace-resolution callbacks |
| trace_wall_duration_ms | Sum of recorded Trace end-start intervals with timing coverage; never sums span durations |
| tool_errors, llm_errors | Actual failed operation signals from the existing engine; complete-case coverage, partial counts lower bounds |
| category.* | Cases with each diagnostic category present, rather than finding count; zero only if trace evidence exists |
| input_tokens, output_tokens | Sum of valid recorded nonnegative integer LLM counts; reporting-span / loaded-LLM-span coverage |
| recovery_rate | Explicit recovered / (recovered + unrecovered) complete executions; unknown/failure-free excluded |
| recovery_coverage, trace_coverage | Eligible recovery cases and attached/complete trace coverage against evaluated cases |

Absent token values stay null; recorded zero remains zero. Booleans, negatives and
noninteger token counts are excluded. Token coverage is over loaded spans, so an omitted
span can still hide usage. Partial evidence never establishes complete error/token totals.
Inclusive parent/child or concurrent durations are never labeled overall elapsed time.

`make_run` records dataset/version/configuration/provenance and validates case digests
and attached diagnostic parameters. Local artifacts serialize with `model_dump_json()`;
reload with `EvaluationRun.model_validate_json(...)`. Evaluation artifacts are trusted
local records, not a public ingestion contract or signed audit log.

`compare_runs(baseline, candidate)` matches stable case IDs and verifies task/input/
criterion digests, dataset/version and configuration. Missing/mismatched cases, unknown
outcomes, analysis-only runs and mixed improvement/regression produce `inconclusive`.
Otherwise failed→success is improvement, success→failed regression, equal outcomes
unchanged. Aggregate differences are candidate minus baseline and require matching case
sets and metric denominators/coverage **on the same cases**, not just equal aggregate
coverage counts. Diagnostic metric differences additionally require
matching diagnostic versions. Full runs preserve configuration and provenance in the report.
Results are descriptive; there is no significance test, causal repair claim or automatic
selection/application of a candidate. Performance changes alone do not change the task
outcome classification. One fixture is not a representative quality benchmark.

## Example workflow on Windows

No service or provider is required. In PowerShell, from the project root:

```powershell
Set-Location D:\Agent_Debugger\backend
$env:PYTHONPATH = (Get-Location).Path
..\.tools\packages\bin\uv.exe run --offline python ..\examples\evaluation_local.py
```

`PYTHONPATH` here is only the current shell's import path so the example can load backend
modules; it changes no system configuration. A script using spawn must keep execution
inside `if __name__ == "__main__":`. Existing dependencies are reused.

The deterministic arithmetic example registers baseline and candidate functions, executes
the same `add-2-3` case, and prints a structured comparison. Baseline returns `-1`,
candidate `5`; validation reports failed→success, success-rate difference +1.0 and
`direction=improvement`. This checks the declared arithmetic criterion only. No trace
exists in this example, so trace error/duration/usage/recovery metrics remain null with
zero coverage. No LLM, repair suggestion or code-change workflow is involved.

Focused development checks cover all 22 existing diagnostic fixtures, config changes,
missing/malformed/truncated evidence, validators, unknown labels, matching/mismatched
comparisons, concurrent timing, usage coverage, recovery, sync/async registered execution,
exceptions, output limits, timeouts and repeatable mock outcomes. See `VERIFICATION.md`
for actual results. New real PostgreSQL snapshot replay, SDK→runner→HTTP→PostgreSQL,
frontend/browser acceptance, full-suite regression and performance benchmarking remain
**not executed in Phase 5C1**, as requested. They are final acceptance work after 5C2.

## Phase 5C2: linking a suggestion to new executions

`FixValidationService(runner).validate(response, suggestion_id, dataset, ...)` is a local
Python service API. It accepts an existing trusted `FixResponse` (for example obtained
from the existing suggestion API) and selects an unambiguous suggestion ID. It checks
trace/project identity, stable finding IDs, span relationships and supporting evidence
references before any execution. A bare ID without its source response is insufficient;
suggestions are stateless, so the developer supplies the previously obtained response.
Local artifact validation checks consistency, not authenticity of arbitrary edited JSON.

The caller supplies two `ImplementationReference` values with registered names,
implementation revision IDs and configuration IDs. Both functions must already be
registered on the 5C1 runner, must be distinct, and revision/configuration identifiers
must describe a change. These IDs are provenance labels, never import paths or executable
configuration. Config changes should be implemented in distinct trusted wrapper functions;
the workflow does not mutate their environment or edit code. Source hashes cover the
registered function only, not its transitive dependencies; record relevant revisions in
additional provenance. A developer must review whether the supplied candidate actually
implements the intended change.

`DeveloperChange(approved=True, approved_by=..., description=...)` records explicit local
developer approval. False/coerced-string approval is rejected. This is an audit assertion
from trusted host code, not an authentication or authorization service. There is no HTTP
endpoint to execute Agents, arbitrary code field, shell interpolation or model command
execution. Proposal wording is never evaluated as Python.

The dataset is canonicalized into immutable bytes and decoded separately for baseline
and candidate. Both receive the same tasks, inputs, explicit validators and runner/rule
configuration. Spawned sync/async functions actually execute, producing fresh outputs and
when instrumented, new trace IDs. `resolve_trace` reuses the existing snapshot/replay
interfaces to attach deterministic diagnostics. An unresolved/partial trace retains task
outcomes but reports missing evidence. This does not alter historical trace records.

```python
from app.schemas.fix_validation import DeveloperChange, ImplementationReference
from app.services.evaluation_runner import TrustedEvaluationRunner
from app.services.fix_validation import FixValidationService

runner = TrustedEvaluationRunner()
runner.register("baseline", baseline_function)   # importable, developer-controlled
runner.register("candidate", candidate_function)
result = FixValidationService(runner).validate(
    existing_fix_response, selected_suggestion_id, dataset,
    validation_id="experiment-001",
    baseline=ImplementationReference(registered_name="baseline", implementation_id="commit-v1",
                                     configuration_id="policy-v1"),
    candidate=ImplementationReference(registered_name="candidate", implementation_id="commit-v2",
                                      configuration_id="policy-v2"),
    change=DeveloperChange(approved=True, approved_by="developer",
                           description="Reviewed local fallback after catalog lookup failure"),
    configuration=configuration, resolve_trace=resolve_trace,
    provenance={"fixture_version": "catalog-1"},
)
```

`FixValidationResult` schema `5c2.1` contains the selected suggestion (including finding/
span/evidence references), source trace/analysis digest, dataset identity/content digest,
configuration, developer change record, implementation IDs, both complete evaluation
runs, per-case outcomes, metric differences, classification, new trace IDs, missing
evidence, trade-offs and limitations. Serialize with `model_dump_json()` and reload with
`FixValidationResult.model_validate_json(...)`. Artifacts do not embed executable code.
They can contain outputs and metadata; keep them local in `.evaluation-artifacts/`.

The classification uses explicit task outcomes via the 5C1 comparison. Fewer findings
do not automatically improve task correctness. Missing traces can still permit an
explicit output-validator comparison, while runtime evidence remains unavailable.
If a returned trace ID reuses the suggestion source, crosses baseline/candidate, or
repeats across cases, the outer validation classification is inconclusive. The embedded
task comparison remains descriptive; trace provenance has not been established.
Positive comparable error/duration/token deltas are exposed as trade-offs to review;
missing/incomparable measurements stay unknown. Candidates are never auto-selected,
applied, deployed or marked universally effective.

### Runnable local mock demonstration

From `D:\Agent_Debugger\backend` in PowerShell:

```powershell
$env:PYTHONPATH = (Get-Location).Path
..\.tools\packages\bin\uv.exe run --offline --with-editable ../sdk python ../examples/fix_validation_local.py --output ../.evaluation-artifacts/catalog
```

The demo explicitly registers two trusted functions from `app/fix_validation_demo.py`.
The baseline uses the remote catalog mock without fallback; the async candidate enables
a developer-supplied local catalog fallback. Both execute normal and outage tasks with
the same structured expected-answer validator. Steps are Plan (LLM-like), Remote catalog
(Tool), optional Local catalog fallback (Tool), and Final answer (LLM-like). Only mock
values are used; no real model or paid API is contacted.

The **real SDK** creates OTel identifiers, hierarchy, status and exception/recovery events.
Its existing `APIExporter` uses an explicit HTTPX `MockTransport` that validates the
existing Trace/Span contracts and saves snapshots locally. This exercises SDK export
serialization, not a real HTTP server or PostgreSQL. The demo first captures a source
outage and generates a real deterministic TOOL_POLICY proposal with disabled AI; the
workflow then performs four new executions (two baseline, two candidate).

Actual local example results:

| Task / metric | Baseline | Candidate |
| --- | --- | --- |
| Available catalog / task criterion | success | success |
| Catalog outage / task criterion | failed | success |
| Task success rate | 1/2 = 50% | 2/2 = 100% |
| Recorded failed Tool operations | 1 | 1 |
| Outage recorded outcome | unrecovered_execution | recovered_execution |
| Explicit recovery rate | 0/1 | 1/1 |
| Recorded input/output tokens | null / null | null / null |

The comparison is improvement on these two declared fixtures, success-rate delta +0.5.
The candidate's initial tool failure remains visible; successful recovery does not erase
it. Timing varies between processes and is reported, not treated as model performance.
Files: five per-trace snapshots, `source-suggestions.json`, `validation.json`. Trace IDs
are real generated IDs in the local artifacts but are not links to persisted database
records. For database-backed use, the host resolver can load the newly exported trace
with the existing SQL repository and expose `/api/v1/traces/{trace_id}` references.

### Execution and acceptance limits

The existing process deadline includes startup and terminates the managed worker, with
bounded cleanup grace. A timeout on a synchronous function is **not a general guarantee
that all underlying computation terminated**: work outside that worker, independently
started descendants, provider jobs and side effects can remain. Host-side trace resolvers
need their own finite timeouts. No CPU/memory quota or security sandbox is claimed.
The existing 1,000-case, per-case deadline and output limits are reused; no concurrency
or infrastructure changes were introduced.

Repeated experiments can call `validate` with new validation IDs and retain each artifact;
there is no automated repeat scheduler, statistical significance or causal attribution.
Full PostgreSQL/HTTP/SDK/frontend/browser acceptance, production/provider tests and broad
performance/variability benchmarks remain **not executed in Phase 5C2**. Focused checks
and the local fixture do not substitute for them. See `VERIFICATION.md` for actual runs.

### Final QA real transport checkpoint (2026-10-13)

Final QA subsequently added `backend/tests/postgres/test_fix_validation_http.py`. Unlike
the local MockTransport demonstration above, its trusted sync baseline/async candidate
workers export new SDK traces over actual TCP to FastAPI and PostgreSQL. The test uses
an isolated migrated schema and a scripted AI provider with explicit consent; no paid
model call is made. The same driver also ran against existing Compose services with AI
consent withheld, retaining five new public fixture traces for browser acceptance.

`.evaluation-artifacts/final-qa-http/validation.json` links the source suggestion and four
new persisted execution IDs to baseline/candidate cases and provenance. Task criteria
passed 1/2 → 2/2, Tool errors stayed 1 → 1, explicit recovery was 0/1 → 1/1, and unrecorded
token metrics remained null. The new candidate graph, exception and recovery event were
inspected in the browser. This closes the earlier real HTTP/PostgreSQL transport deferral
for this workflow; it does not establish generalized quality or statistical repair efficacy.
Full regression counts, exact Trace IDs and remaining publication/browser fault checks
are recorded in `VERIFICATION.md`. Existing timeout/trust/provenance limitations still apply.
