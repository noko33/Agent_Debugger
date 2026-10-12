"""Reviewable proposals; no executable patches, success claims or client-supplied analysis."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StringConstraints

from app.schemas.ai_diagnosis import AIDiagnosisResponse, ContextFact, ShortText
from app.schemas.traces import Contract, SpanId, TraceId

SuggestionCategory = Literal["PROMPT", "TOOL_POLICY", "RUNTIME", "INSTRUMENTATION"]
FindingId = Annotated[str, StringConstraints(pattern=r"^f_[0-9a-f]{24}$")]
SuggestionId = Annotated[str, StringConstraints(pattern=r"^s_[0-9a-f]{24}$")]


class FixRequest(Contract):
    include_ai: StrictBool = False
    consent_to_external_processing: StrictBool = False


class Suggestion(Contract):
    suggestion_id: SuggestionId
    category: SuggestionCategory
    title: ShortText
    explanation: ShortText
    basis: Literal["recorded_observation", "heuristic_warning", "data_quality"]
    related_finding_ids: list[FindingId]
    span_ids: list[SpanId]
    supporting_evidence: list[str]
    hypothesis_ids: list[str] = Field(default_factory=list)
    proposed_change: ShortText
    expected_observable_improvement: ShortText
    risks_and_tradeoffs: list[ShortText] = Field(min_length=1)
    preconditions: list[ShortText] = Field(min_length=1)
    missing_evidence: list[ShortText] = Field(min_length=1)
    validation_plan: list[ShortText] = Field(min_length=1)
    status: Literal["proposed_unvalidated"] = "proposed_unvalidated"


class FixResponse(Contract):
    schema_version: Literal["1"] = "1"
    suggestions_version: Literal["5b.1"] = "5b.1"
    trace_id: TraceId
    project_id: UUID
    analysis: AIDiagnosisResponse
    finding_ids: list[FindingId]
    evidence: list[ContextFact]
    suggestions: list[Suggestion]
    omitted_suggestion_count: int = 0
    limitations: list[str] = [
        "All suggestions require developer review and controlled validation; none is applied.",
        "Recorded patterns and model hypotheses do not establish a confirmed root cause.",
        "Expected improvements are validation targets, not measured repair effectiveness.",
    ]
