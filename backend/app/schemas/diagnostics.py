"""Versioned deterministic findings; evidence is deliberately allowlisted."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field, JsonValue

from app.schemas.traces import Contract, SpanId, TraceId


class Category(StrEnum):
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    LLM_EXECUTION_ERROR = "LLM_EXECUTION_ERROR"
    REPEATED_TOOL_FAILURE = "REPEATED_TOOL_FAILURE"
    POSSIBLE_RETRY_LOOP = "POSSIBLE_RETRY_LOOP"
    SLOW_OPERATION = "SLOW_OPERATION"
    INCOMPLETE_OR_INVALID_TRACE = "INCOMPLETE_OR_INVALID_TRACE"


class DiagnosticParameters(Contract):
    repeated_tool_failure_threshold: int = Field(default=2, ge=2, le=1000)
    retry_loop_threshold: int = Field(default=3, ge=3, le=1000)
    slow_operation_ms: float = Field(default=1000, gt=0, le=86_400_000, allow_inf_nan=False)
    max_spans: int = Field(default=5000, ge=1, le=100_000)


class Evidence(Contract):
    span_id: SpanId | None = None
    signal: str
    values: dict[str, JsonValue] = Field(default_factory=dict)


class Finding(Contract):
    rule_id: str
    category: Category
    severity: StrEnumSeverity
    nature: StrEnumNature
    summary: str
    explanation: str
    trace_id: TraceId
    span_ids: list[SpanId]
    evidence: list[Evidence]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class StrEnumSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class StrEnumNature(StrEnum):
    OBSERVED_FAILURE = "observed_failure"
    HEURISTIC_WARNING = "heuristic_warning"
    DATA_QUALITY = "data_quality"


class ExecutionOutcome(StrEnum):
    RECORDED_SUCCESS = "recorded_success"
    RECOVERED = "recovered_execution"
    UNRECOVERED = "unrecovered_execution"
    UNKNOWN = "unknown"


class Outcome(Contract):
    classification: ExecutionOutcome
    explanation: str
    evidence: list[Evidence]


class DiagnosticResponse(Contract):
    schema_version: str = "1"
    rules_version: str = "4a.1"
    trace_id: TraceId
    project_id: UUID
    coverage: str = "all_currently_stored_spans"
    span_count: int
    operation_failure_count: int
    repeated_failure_group_count: int
    parameters: DiagnosticParameters
    outcome: Outcome
    findings: list[Finding]
