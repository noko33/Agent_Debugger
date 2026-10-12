"""Local evaluation artifacts, separate from recorded execution status."""

from typing import Literal, Self

from pydantic import Field, JsonValue, StrictBool, model_validator

from app.schemas.diagnostics import DiagnosticParameters, DiagnosticResponse
from app.schemas.traces import Contract, Name, TraceId

TaskOutcome = Literal["success", "failed", "unknown"]
Direction = Literal["improvement", "regression", "unchanged", "inconclusive"]


class ValidationCriterion(Contract):
    kind: Literal["exact_match", "structured_values"]
    expected: JsonValue

    @model_validator(mode="after")
    def structured_object(self) -> Self:
        if self.kind == "structured_values" and not isinstance(self.expected, dict):
            raise ValueError("structured_values requires an expected object")
        return self


class EvaluationCase(Contract):
    case_id: Name
    task: str = Field(min_length=1, max_length=10_000)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    criterion: ValidationCriterion | None = None


class EvaluationDataset(Contract):
    dataset_id: Name
    version: Name
    cases: list[EvaluationCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        return self


class RunnerConfiguration(Contract):
    timeout_seconds: float = Field(default=5, gt=0, le=300, allow_inf_nan=False)
    max_output_bytes: int = Field(default=65_536, ge=128, le=1_048_576)
    diagnostics: DiagnosticParameters = Field(default_factory=DiagnosticParameters)


class AgentOutput(Contract):
    value: JsonValue = None
    trace_ids: list[TraceId] = Field(default_factory=list, max_length=100)


class TraceObservation(Contract):
    snapshot_digest: str
    analysis: DiagnosticResponse
    complete: StrictBool
    wall_duration_ms: float | None
    tool_errors: int
    llm_errors: int
    llm_span_count: int
    input_tokens: int | None
    output_tokens: int | None
    input_token_coverage: int
    output_token_coverage: int


class EvaluationResult(Contract):
    case_id: Name
    case_digest: str
    outcome: TaskOutcome
    validation_basis: Literal["validator", "explicit_label", "execution_error", "none"]
    output: JsonValue = None
    error: Literal["agent_error", "timeout", "invalid_output", "worker_error"] | None = None
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    trace_ids: list[TraceId] = Field(default_factory=list)
    observation: TraceObservation | None = None
    observation_issue: Literal["unresolved", "multiple_traces"] | None = None


class MetricResult(Contract):
    name: str
    value: float | None
    denominator: int
    observed: int
    unit: Literal["count", "fraction", "ms", "tokens"]
    explanation: str


class EvaluationRun(Contract):
    schema_version: Literal["5c1.1"] = "5c1.1"
    run_id: Name
    dataset_id: Name
    dataset_version: Name
    agent: Name
    mode: Literal["trusted_execution", "analysis_only"]
    configuration: RunnerConfiguration
    provenance: dict[str, str]
    results: list[EvaluationResult]
    metrics: list[MetricResult]

    @model_validator(mode="after")
    def unique_results(self) -> Self:
        if len({r.case_id for r in self.results}) != len(self.results):
            raise ValueError("result case IDs must be unique")
        return self


class CaseComparison(Contract):
    case_id: str
    baseline: TaskOutcome | None
    candidate: TaskOutcome | None
    direction: Direction
    reason: str


class BaselineCandidateComparison(Contract):
    baseline_run: EvaluationRun
    candidate_run: EvaluationRun
    cases: list[CaseComparison]
    metric_differences: dict[str, float | None]
    direction: Direction
    limitations: list[str]
