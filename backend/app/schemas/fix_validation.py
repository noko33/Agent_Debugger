"""Developer-approved local experiments; no executable configuration in artifacts."""

from typing import Literal

from pydantic import Field, StrictBool

from app.schemas.evaluation import BaselineCandidateComparison, Direction, RunnerConfiguration
from app.schemas.fix_suggestions import Suggestion
from app.schemas.traces import Contract, Name, TraceId


class ImplementationReference(Contract):
    registered_name: Name
    implementation_id: Name
    configuration_id: Name


class DeveloperChange(Contract):
    approved: StrictBool
    approved_by: Name
    description: str = Field(min_length=1, max_length=2000)


class FixValidationResult(Contract):
    schema_version: Literal["5c2.1"] = "5c2.1"
    validation_id: Name
    suggestion: Suggestion
    source_trace_id: TraceId
    source_analysis_digest: str
    dataset_id: Name
    dataset_version: Name
    dataset_digest: str
    configuration: RunnerConfiguration
    baseline: ImplementationReference
    candidate: ImplementationReference
    developer_change: DeveloperChange
    comparison: BaselineCandidateComparison
    classification: Direction
    trace_ids: list[TraceId]
    missing_evidence: list[str]
    tradeoffs: list[str]
    limitations: list[str]
