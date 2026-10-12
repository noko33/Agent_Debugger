"""Model hypotheses are separate from recorded deterministic findings."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StringConstraints

from app.schemas.diagnostics import DiagnosticResponse
from app.schemas.traces import Contract, JsonValue, SpanId, TraceId

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
Cause = Literal[
    "tool_operation_failure",
    "llm_operation_failure",
    "repeated_operation_failure",
    "possible_retry_pattern",
    "performance_bottleneck",
    "trace_data_gap",
    "cannot_determine",
]


class AnalysisRequest(Contract):
    consent_to_external_processing: StrictBool = False


class Hypothesis(Contract):
    hypothesis_id: Annotated[str, StringConstraints(pattern=r"^h[1-9][0-9]{0,2}$")]
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    claim_kind: Literal["hypothesis"]
    cause_type: Cause
    possible_root_cause: ShortText
    supporting_evidence: list[Annotated[str, StringConstraints(max_length=100)]] = Field(
        max_length=20
    )
    span_ids: list[SpanId] = Field(max_length=20)
    missing_evidence: list[ShortText] = Field(min_length=1, max_length=10)
    limitations: list[ShortText] = Field(min_length=1, max_length=10)
    support_status: Literal["supported", "weakly_supported", "cannot_be_determined"]


class ModelOutput(Contract):
    hypotheses: list[Hypothesis] = Field(max_length=5)


class ContextFact(Contract):
    evidence_id: str
    source: Literal["span_record", "rule_finding", "recorded_outcome"]
    span_ids: list[SpanId]
    values: dict[str, JsonValue]


class ContextReport(Contract):
    policy_version: Literal["5a.1"] = "5a.1"
    stored_span_count: int
    included_span_ids: list[SpanId]
    omitted_span_count: int
    omitted_finding_count: int
    truncated: bool
    serialized_context_bytes: int
    input_budget_units: int
    input_budget_method: Literal["utf8_bytes_plus_512_framing_reserve"] = (
        "utf8_bytes_plus_512_framing_reserve"
    )
    missing_evidence: list[str]


class ModelExecution(Contract):
    status: Literal[
        "completed", "disabled", "consent_required", "not_needed", "rate_limited", "failed"
    ]
    model: str | None = None
    attempts: int = 0
    elapsed_ms: float = 0
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    error_code: str | None = None
    message: str


class AIDiagnosisResponse(Contract):
    schema_version: Literal["1"] = "1"
    trace_id: TraceId
    deterministic: DiagnosticResponse
    context: ContextReport
    evidence: list[ContextFact]
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    execution: ModelExecution
    limitations: list[str] = [
        "Hypotheses are model-generated explanations, never confirmed root causes.",
        "Citation validation establishes reference integrity, not semantic entailment.",
        "Recorded recovery and success do not establish semantic task correctness.",
    ]
