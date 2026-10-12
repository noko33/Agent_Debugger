# Phase 5B: evidence-grounded repair suggestions

The implementation, automated regressions and real HTTP/PostgreSQL checks are available.
Actual browser interaction acceptance is pending because the installed browser automation
tools could not safely operate the current session. See `VERIFICATION.md` for actual results.
Suggestions are unvalidated proposals; Agent Debugger never applies or executes them.

## Architecture

The existing Diagnostics Panel explicitly requests
`POST /api/v1/traces/{trace_id}/fix-suggestions?project_id=<uuid>`.
The router reuses Phase 5A's bounded consistent snapshot dependency and releases the
database transaction before optional external analysis. `FixSuggestionService` obtains
the existing deterministic findings and optional validated hypotheses; the pure
`generate_suggestions` engine maps evidence to versioned, vetted templates.

No database migration, persisted analysis, SDK change, background worker or dependency
was added. Deterministic rules and Phase 5A output contracts remain unchanged.
The new modules are `schemas/fix_suggestions.py`, `services/fix_suggestions.py`,
`services/fix_templates.py` and `api/fix_suggestions.py`.

The optional AI path reuses `AIDiagnosisService`, provider transport, allowlist 5a.1,
budget checks, reference validation and shared concurrency/cooldown guard. It performs
at most one hypothesis-analysis operation. Validated hypotheses are associated with
matching findings/spans; the wording and proposed change still come from vetted templates.
There is no second model request for recommendation wording and no free-form model
repair command in the API. Hypothesis links describe unverified explanations, not
additional confirmed observations. Weakly supported hypotheses stay weakly supported
in the embedded analysis; they never upgrade a heuristic recommendation's basis.

Results are stateless. Existing validated results are reused within the current request;
previous responses are not cached or persisted across requests. Client-supplied analysis
objects are rejected rather than treated as server-validated evidence. Requesting AI
again is a new explicit operation subject to the shared Phase 5A cooldown.

## Recommendation categories and evidence gates

| Category | Evidence gate | Proposed action |
| --- | --- | --- |
| `TOOL_POLICY` | Repeated tool-failure finding | Review identity, retryability and bounded fallback/stop policy; labels do not prove identical calls |
| `TOOL_POLICY` | Individual tool failure and recorded unrecovered outcome | Review error handling and test a safe fallback or explicit terminal failure policy |
| `TOOL_POLICY` | Individual tool failure and explicit recorded recovery | Validate existing recovery; do not replace a working fallback merely because a child failed |
| `RUNTIME` | Existing evidence-gated possible retry-loop warning | Review attempt/time limits, idempotency and retryability before testing bounded retries/backoff |
| `RUNTIME` | Recorded slow-operation warning | Profile inclusive child/waiting time before choosing timeout or budget; no arbitrary threshold is prescribed |
| `INSTRUMENTATION` | Structural/data-quality finding | Complete missing intervals/relationships/status/recovery signals |
| `INSTRUMENTATION` | LLM failure, unknown suitable fix, or root-only failure/outcome with no applicable finding | Record safe error/validation/handling metadata rather than infer a prompt defect or service outage |
| `PROMPT` | LLM failure **and** an explicit caller-reported instruction-compliance check failure | Review validator and instructions locally, then test clearer acceptance/fallback instructions only if that check is valid |

For the last gate only, existing span JSONB attributes may optionally contain:

```json
{
  "agent_debugger.validation.kind": "instruction_compliance",
  "agent_debugger.validation.passed": false
}
```

These fields are caller assertions, not independent validation by Agent Debugger. The
value must be the boolean `false`, not a string. They do not modify the diagnostic engine
or SDK and are not added to Phase 5A's external allowlist. No prompt/completion content
is sent. Without that gate, a plain LLM error produces an instrumentation suggestion,
not a speculative prompt edit. A successful trace with no meaningful finding produces
no suggestion; this does not prove semantic success.

All proposals distinguish recorded observations from heuristic warnings/data quality.
They include missing evidence even when the recorded pattern is clear: current policies,
semantic acceptance criteria and a controlled baseline are generally not known.
An observed unrecovered outcome does not prove that the referenced tool caused it.

## API contract

Default request, requiring no external provider:

```json
{"include_ai": false, "consent_to_external_processing": false}
```

AI enrichment requires `include_ai=true`, `consent_to_external_processing=true` and
the existing operator `AI__ENABLED=true` configuration. Neither opening a trace nor
requesting deterministic suggestions invokes a model. Compose remains disabled by
default. Configuring a live provider or invoking it for paid tests needs separate approval.

The versioned response contains:

- `schema_version="1"`, `suggestions_version="5b.1"`, trace/project IDs.
- `analysis`: the existing Phase 5A response, including deterministic findings, recorded
  outcome, optional hypotheses, context coverage and model execution metadata.
- `finding_ids`: stable IDs derived from trace ID, rule ID and sorted finding span IDs.
- `evidence`: sanitized local finding/outcome references; no raw tool labels/error text.
  Finding values include rule ID, category, nature and related span count. Prompt-check
  evidence additionally includes only the fixed check kind and boolean result.
- `suggestions`: at most 100 proposals in stable identifier order.
- `omitted_suggestion_count` and global limitations.

Each proposal includes `suggestion_id`, `category`, `title`, `explanation`, `basis`,
`related_finding_ids`, actual `span_ids`, `supporting_evidence`, optional `hypothesis_ids`,
`proposed_change`, `expected_observable_improvement`, `risks_and_tradeoffs`,
`preconditions`, `missing_evidence`, `validation_plan` and
`status="proposed_unvalidated"`.
Suggestion IDs are SHA-256-derived identifiers over trace, version, template and sorted
span IDs. Evidence/finding IDs are local identifiers, not persisted finding records.
Outcome-only instrumentation suggestions have no finding ID; they cite recorded outcome
evidence instead of inventing a finding or span. Invalid/missing stored span references
are not reconstructed. The unchanged deterministic analysis may still describe missing
references as data-quality evidence.

Duplicate proposals have one ID and are deduplicated. Grouped repeated failures suppress
redundant individual tool-policy proposals for the same spans. Retry and profiling
proposals remain separate review tasks. No competing numeric timeout/retry settings are
generated. Changes to template meaning require a suggestion-version review.

Operational AI failure returns HTTP 200 with failed analysis metadata and the same
deterministic suggestions still available. Existing snapshot errors remain 404 for wrong
project/missing trace, 413 for span-limit overflow, 422 for invalid requests/stored
contracts and 503 for unavailable database. Project scope is not authentication.

Windows example for the retained seven-span trace:

```powershell
$traceId = '2647313fd3a662d1eb2663982fc4031a'
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/api/v1/traces/$traceId/fix-suggestions" `
  -ContentType 'application/json' `
  -Body '{"include_ai":false,"consent_to_external_processing":false}'
```

## Example and review workflow

The retained sample's Optional enrichment failure yields a `TOOL_POLICY` proposal titled
**Validate the existing recovery policy**. Its supporting registry entry references the
existing `tool_execution_error.v1` finding and span `518cc84e627bd3df`. Recorded recovery
is preserved. The proposal asks the developer to keep current behavior until reviewed
and exercise the fallback under controlled failure. Expected improvement is a test target:
consistent recovery metadata and locally defined result checks. Risks include incomplete
answers and regression from replacing a working fallback.

For repeated failures, the proposal instead asks the developer to verify tool identity,
classify retryable failures and review a bounded fallback/stop policy. This is not a claim
of an infinite loop. Proposed retry bounds require the stronger existing retry metadata.

The validation workflow is manual:

1. Inspect referenced spans, recorded outcome, missing evidence and limitations.
2. Verify tool/validator identity, side effects, acceptance criteria and current policy.
3. Capture a redacted baseline and change one reviewed policy locally.
4. Exercise comparable success, failure and side-effect cases.
5. Compare outcome, explicit recovery, attempts, durations and semantic result checks.
   Keep a change only when predefined checks pass without unacceptable regressions.

The displayed plan is not a Phase 5C replay runner. There is no Apply/Fix/Deploy action.

## UI, privacy and limitations

The existing Diagnostics Panel adds Request suggestions, optional AI and separate consent
checkboxes, evidence/risks/preconditions/validation details, and span-navigation buttons.
Default requests are deterministic. Navigation uses the existing selected-span state
shared by graph, timeline and detail; unloaded references are disabled with a notice.
Requests are abortable, trace-scoped and ignore stale success/error results. Findings and
span views can be different read snapshots; no streaming or atomic UI snapshot is claimed.
Text is rendered as React text/JSON, never executable HTML.

No trace strings are interpolated into recommendation wording. The optional model only
receives Phase 5A's allowlisted context; no raw arguments, prompts, credentials or full
results are added. The full local `analysis.deterministic` response retains its original
contract and should not be logged indiscriminately. Existing no-auth/local-only deployment
limits still apply. Never execute model-generated commands.

False recommendations remain possible: tool labels may collide, finite retries may trip
heuristics, long operations may be legitimate, caller validation checks may be wrong,
and recorded recovery may hide a semantic error. Vetted templates are deliberately generic
and cannot select project-specific thresholds or guarantee a repair. Mocked fixture tests
verify mapping, privacy, references and fallback—not real-world repair effectiveness.
External provider quality and live compatibility remain untested without paid approval.

See [verification results](VERIFICATION.md) for tests and the historical browser blocker.
The user's Phase 5C1 request accepts this implementation and defers actual browser
interaction acceptance until final project-wide verification after 5C2. Phase 5C1 adds
an independent [local evaluation foundation](EVALUATION.md), without automatically
executing these proposals. The later 5C2 request authorizes controlled execution of
developer-supplied baseline/candidate functions linked to a suggestion; it never executes
suggestion text or applies edits. See `EVALUATION.md`. Stop for review after 5C2; do not
deploy or push automatically.

Final QA update (2026-10-13): the previously deferred browser request/rendering/evidence
navigation checks passed on existing and newly exported real PostgreSQL traces. A spawned
baseline/candidate workflow retrieved a real HTTP suggestion and validated its references
and new execution artifacts. No proposed edit was applied, and no paid model was called.
See `VERIFICATION.md` for exact results and remaining acceptance/publication limitations.
