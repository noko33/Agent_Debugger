# Phase 4A: deterministic failure analysis

Diagnostics analyze recorded evidence, not prompts or semantic task success. The engine
is a pure Python function (`analyze_trace`), with separate signal, operation/retry,
structure and outcome modules. The service obtains a bounded PostgreSQL snapshot and
returns findings on demand. There are no migrations, persisted findings, providers,
background workers or new dependencies. Phase 4B displays these results in the existing
Explorer; see `TRACE_EXPLORER.md` for evidence navigation and frontend limits.

Phase 5A adds an independent, explicitly requested optional hypothesis API without
changing this rule engine. Its provider policy, consent, schema and verification limits
are documented in [AI_DIAGNOSIS.md](AI_DIAGNOSIS.md). GET diagnostics remains provider-free.

Phase 5B adds explicit review-only repair proposals based on these same findings; see
[FIX_SUGGESTIONS.md](FIX_SUGGESTIONS.md). It does not change rule classification or
apply any proposed change.

## Taxonomy and evidence requirements

| Category / rule ID suffix `.v1` | Condition | Nature / severity |
| --- | --- | --- |
| `TOOL_EXECUTION_ERROR` / `tool_execution_error` | TOOL operation with explicit Error status, or Unset status plus an `error.type` string/exception event | observed failure / error |
| `LLM_EXECUTION_ERROR` / `llm_execution_error` | Same condition for an LLM operation; provider/model need not be present | observed failure / error |
| `REPEATED_TOOL_FAILURE` / `repeated_tool_failure` | At least 2 failed tool spans sharing exact `gen_ai.tool.name`, falling back to recorded span name | observed failure / error |
| `POSSIBLE_RETRY_LOOP` / `possible_retry_loop` | At least 3 failed, explicitly numbered consecutive retry attempts with matching identity, parent, input fingerprint and retry group; valid non-overlapping intervals | heuristic warning / warning |
| `SLOW_OPERATION` / `slow_operation` | Valid recorded end minus start strictly exceeds 1,000 ms | heuristic warning / warning |
| `INCOMPLETE_OR_INVALID_TRACE` / `incomplete_or_invalid_trace` | Missing trace/span end, no stored spans, missing parent, cycle/self-link, duplicate/mismatched IDs, reversed or out-of-bounds times, or conflicting root/trace status | data quality / warning |

Exception evidence means an event named `exception`, an event with a nonempty string
`exception.type`, or a nonempty string span attribute `error.type`. A final OK span with
an exception event can have handled that exception locally; it is not classified as a
failed operation. Error status alone is explicit evidence even when no exception exists.
Runtime/Agent failures contribute to `operation_failure_count` and outcome evidence;
there is no speculative extra failure category for them.

Repeated failure counts are independent of arguments, sequencing and recovery. Concurrent
failed tool calls can be repeated failures, but are not evidence of a retry loop. Equal
timestamps are not used to infer chronology. Labels can collide across tool implementations;
the explanation identifies this limitation rather than asserting identical operations.

### Optional explicit retry evidence

Existing attributes can carry these caller-supplied fields; the SDK is unchanged and
does not automatically emit them:

```json
{
  "gen_ai.tool.name": "lookup",
  "agent_debugger.retry.group_id": "catalog-retry",
  "agent_debugger.retry.attempt": 1,
  "agent_debugger.operation.input_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

The fingerprint must be a 64-character lowercase hex opaque value. It is used for exact
equality only; no raw arguments are read, captured or hashed by diagnostics. A caller
must apply its own redaction/privacy policy before supplying fingerprints (hashes of
low-entropy sensitive inputs can still leak information). Group IDs must be nonempty
strings up to 200 characters; attempt numbers must be integers 1..1,000,000, excluding
booleans. Within each candidate group, attempt numbers must be consecutive and unique,
all calls must fail, and each previous end must be on/before the next start. Overlapping,
incomplete or duplicate/gapped attempts do not qualify. Available model/provider labels
must match too. A same-parent retry group containing incomplete metadata is excluded.

Without these fields there is **no loop finding**, even for many same-name errors.
Matching fingerprints are caller assertions, not verified identity of raw arguments.
An intentional finite retry policy can still trigger the heuristic: it never means a
proven infinite loop. Success within the fully identified candidate group suppresses
that warning, while earlier repeated failures remain observable.

## Configuration

Pydantic Settings validates defaults and supports these optional environment variables:

```dotenv
DIAGNOSTICS__REPEATED_TOOL_FAILURE_THRESHOLD=2
DIAGNOSTICS__RETRY_LOOP_THRESHOLD=3
DIAGNOSTICS__SLOW_OPERATION_MS=1000
DIAGNOSTICS__MAX_SPANS=5000
```

Repeated failure threshold range: 2..1,000. Retry threshold: 3..1,000. Slow threshold:
finite, greater than 0 and at most 86,400,000 ms. Span limit: 1..100,000; the default
5,000 protects request memory/latency. Native settings examples are in `backend/.env.example`.
Thresholds used are included in the response and each configurable finding. They are
server settings, not arbitrary query overrides. The existing Compose file is unchanged
and does not forward these new optional variables; its backend uses the defaults.
Native backend runs can configure them through their process environment or `.env`.

Durations are inclusive and derived from aware timestamps, preserving microseconds.
Parent/child or concurrent durations are never summed into wall-clock execution time.
A slow span is not necessarily a correctness failure. No exclusive time or critical
path is inferred.

## Recorded execution outcomes

Operation findings and the overall outcome are separate:

- `recorded_success`: completed trace or unique Agent root explicitly reports OK, no
  observed operation failures, and no detected structural problems. This is recorded
  execution status, not proof that the Agent answered correctly.
- `recovered_execution`: a unique completed Agent root is OK, with an explicit root
  event named `recovery` inside its interval and at/after **all** observed failed span
  ends. No structural/status contradiction is present. This is self-reported recovery;
  the current event does not map individual failures to fallback calls.
- `unrecovered_execution`: a completed, structurally consistent trace or unique root
  explicitly reports Error. A recovery event cannot override that final Error status.
- `unknown`: insufficient, incomplete, inconsistent or ambiguous evidence. Child errors
  plus an OK root or a later successful span alone do not prove recovery or overall failure.

The Phase 2B example's root OK plus `recovery` event qualifies as recovered; Optional
enrichment still has an operation failure finding. Removing that event leaves overall
outcome unknown even if Fallback succeeds. A final Error root/trace is unrecovered.
Incomplete timing, missing parents or conflicting root/trace status make the overall
classification conservative (unknown), while explicit operation errors remain findings.

## Read-only API and response contract

```http
GET /api/v1/traces/{trace_id}/diagnostics
GET /api/v1/traces/{trace_id}/diagnostics?project_id={uuid}
```

The optional project scope must match the stored trace; mismatch and absent trace both
return 404. This follows the existing globally unique trace IDs/project filter convention.
There is currently no authentication or tenant isolation; this filter is not authorization.
Continue using loopback-only local services.

The repository takes a trace SHARE row lock, conflicting with ingestion's existing
FOR UPDATE lock until the request transaction finishes. It loads all spans/events after
checking the limit, so ingestion cannot change the graph between those reads. This is
consistent with the existing writer convention, not a guarantee for direct SQL writers
that bypass it. There is no pagination or partial successful diagnostic response.
`coverage=all_currently_stored_spans` does not assert that every instrumented span was
exported; sampling/dropped exports and the Trace-then-Span SDK export window still exist.

Responses use `schema_version=1`, `rules_version=4a.1`, trace/project IDs, span and failure
counts, effective parameters, outcome (classification, explanation, evidence), and findings.
Every finding has stable rule/category IDs, severity, nature, summary, explanation,
trace ID, related span IDs, supporting evidence and rule parameters. Findings are sorted
deterministically; equal-time order is only a stable presentation order.

Evidence allowlists IDs, status/kind, times, durations, tool labels, event indices,
retry attempt numbers and equality checks. Raw fingerprints/group IDs, arbitrary JSON,
exception messages/types, status messages, prompts, completions and arguments are not
echoed into diagnostics. Recorded span/tool names remain user-controlled metadata.
There are no confidence percentages or claimed root causes.

Example PowerShell request against the retained real example:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/traces/2647313fd3a662d1eb2663982fc4031a/diagnostics
```

Selected fields from the actual verified response (other contract fields omitted here):

```json
{
  "schema_version": "1",
  "rules_version": "4a.1",
  "trace_id": "2647313fd3a662d1eb2663982fc4031a",
  "span_count": 7,
  "operation_failure_count": 1,
  "repeated_failure_group_count": 0,
  "coverage": "all_currently_stored_spans",
  "outcome": {"classification": "recovered_execution"},
  "findings": [{
    "rule_id": "tool_execution_error.v1",
    "category": "TOOL_EXECUTION_ERROR",
    "severity": "error",
    "nature": "observed_failure",
    "span_ids": ["518cc84e627bd3df"],
    "parameters": {},
    "evidence": [{
      "span_id": "518cc84e627bd3df",
      "signal": "operation_status",
      "values": {"status_code": "error", "operation_type": "tool"}
    }]
  }]
}
```

Full Pydantic contracts are in `backend/app/schemas/diagnostics.py` and OpenAPI `/docs`.

| Condition | HTTP result |
| --- | --- |
| Missing trace or mismatched project | 404 `trace_not_found` |
| Invalid ID/query | 422 `validation_error` |
| Stored data violates Pydantic read contracts | 422 `invalid_stored_trace`, generic message without raw data |
| Valid stored contracts but missing parents/cycles/ends | 200 with data-quality findings and conservative outcome |
| Stored span count exceeds configured bound | 413 `diagnostic_span_limit`; no partial findings |
| Database unavailable, timeout or lock operation failure | Existing 503 `database_unavailable` handling |

## Evaluation and limits

From `backend/`, `uv run python -m tests.diagnostic_fixtures` prints the 22 labeled-case
evaluation. `tests/test_diagnostics.py` checks rules, outcome labels and determinism;
`tests/postgres/test_diagnostics_postgres.py` persists all labeled cases through the API
and checks the same labels against actual PostgreSQL, plus scope/limits/corruption/locks.
Database tests require the existing isolated `TEST_DATABASE_URL` and generated schemas.

Precision/recall are **per trace, per category presence**, not per finding or real-world
failure cause. Fixtures are deliberately designed regression examples, reused by tests;
they are not an independent benchmark or evidence of production accuracy. Each fixture's
expected categories/outcome is explicit in `tests/diagnostic_fixtures.py`. Actual results,
per-category support counts and test commands are recorded in `VERIFICATION.md`.

Known false-positive risks include tool-label collisions, intentional finite retries,
legitimate long operations, delayed ingestion, sampling and clock offsets. Known false
negatives include unrecorded errors, exception-only operations marked OK, loops without
required metadata, successful-but-wrong outcomes and sensitive/redacted data that removes
evidence. Missing evidence is never fabricated. Wrong-tool selection, hallucinations,
unsafe actions, semantic success, root causes and repair recommendations are unsupported.

Phase 5C1 reuses this unchanged engine for immutable historical analysis replay and
coverage-aware local evaluation; see [EVALUATION.md](EVALUATION.md). Reinterpreting old
evidence does not demonstrate that a repair changes future Agent behavior.
