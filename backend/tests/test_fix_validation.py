import json

import pytest
from pydantic import ValidationError

from app.core.ai_config import AISettings
from app.fix_validation_demo import run_demo
from app.schemas.diagnostics import DiagnosticParameters
from app.schemas.evaluation import (
    AgentOutput,
    EvaluationCase,
    EvaluationDataset,
    RunnerConfiguration,
    ValidationCriterion,
)
from app.schemas.fix_suggestions import FixRequest
from app.schemas.fix_validation import DeveloperChange, FixValidationResult, ImplementationReference
from app.services.ai_diagnosis.service import AIDiagnosisService, AnalysisGuard
from app.services.evaluation import evaluate_case, make_run
from app.services.evaluation_runner import TrustedEvaluationRunner
from app.services.fix_suggestions import FixSuggestionService
from app.services.fix_validation import FixValidationService, _select
from tests.diagnostic_fixtures import labeled_cases, trace
from tests.test_evaluation_runner import excessive_agent, failed_agent, slow_agent


def baseline_agent(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value="incorrect")


async def candidate_agent(case: EvaluationCase) -> AgentOutput:
    value = case.inputs.get("expected", "correct")
    case.inputs.clear()  # Must not mutate the shared dataset or its recorded digest.
    return AgentOutput(value=value)


def historical_agent(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value="correct", trace_ids=[trace().trace_id])


def source():
    c = next(c for c in labeled_cases() if c.name == "repeated_failures_without_retry_metadata")
    return FixSuggestionService(
        AIDiagnosisService(AISettings(enabled=False), DiagnosticParameters(), AnalysisGuard())
    ).analyze((c.trace, c.spans), FixRequest())


def tasks(criterion=True):
    return EvaluationDataset(
        dataset_id="fix-fixture",
        version="1",
        cases=[
            EvaluationCase(
                case_id="one",
                task="Return expected fixture",
                inputs={"expected": "correct"},
                criterion=ValidationCriterion(kind="exact_match", expected="correct")
                if criterion
                else None,
            )
        ],
    )


def execute(
    *,
    before=baseline_agent,
    after=candidate_agent,
    dataset=None,
    config=None,
    response=None,
    suggestion_id=None,
    change=None,
    runner=None,
):
    runner = runner or TrustedEvaluationRunner()
    runner.register("before", before)
    runner.register("after", after)
    response = response or source()
    return FixValidationService(runner).validate(
        response,
        suggestion_id or response.suggestions[0].suggestion_id,
        dataset or tasks(),
        validation_id="fixture-validation",
        baseline=ImplementationReference(
            registered_name="before", implementation_id="v1", configuration_id="baseline-policy"
        ),
        candidate=ImplementationReference(
            registered_name="after", implementation_id="v2", configuration_id="candidate-policy"
        ),
        change=change
        or DeveloperChange(
            approved=True,
            approved_by="test-developer",
            description="Explicit trusted fixture candidate",
        ),
        configuration=config,
    )


def test_complete_workflow_and_frozen_dataset_provenance() -> None:
    data = tasks()
    original = data.model_dump_json()
    result = execute(dataset=data)
    before, after = result.comparison.baseline_run, result.comparison.candidate_run
    assert result.classification == "improvement"
    assert before.results[0].outcome == "failed" and after.results[0].outcome == "success"
    assert before.results[0].case_digest == after.results[0].case_digest
    assert before.configuration == after.configuration == result.configuration
    assert before.run_id != after.run_id
    assert before.provenance["dataset_digest"] == after.provenance["dataset_digest"]
    assert before.provenance["callable_source_digest"] != after.provenance["callable_source_digest"]
    assert data.model_dump_json() == original
    assert result.suggestion.related_finding_ids and result.suggestion.span_ids
    assert result.missing_evidence and result.comparison.metric_differences["input_tokens"] is None
    assert FixValidationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("related_finding_ids", ["f_" + "a" * 24]),
        ("span_ids", ["f" * 16]),
        ("supporting_evidence", ["missing-evidence"]),
    ],
)
def test_invalid_references_rejected_before_execution(monkeypatch, field, value) -> None:
    response = source()
    setattr(response.suggestions[0], field, value)
    runner = TrustedEvaluationRunner()
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="references"):
        execute(response=response, runner=runner)


def test_missing_ambiguous_or_mismatched_source_rejected(monkeypatch) -> None:
    runner = TrustedEvaluationRunner()
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: pytest.fail("must not execute"))
    response = source()
    with pytest.raises(ValueError, match="absent"):
        execute(response=response, suggestion_id="s_" + "f" * 24, runner=runner)
    response.suggestions.append(response.suggestions[0])
    with pytest.raises(ValueError, match="ambiguous"):
        _select(response, response.suggestions[0].suggestion_id)
    response = source()
    response.trace_id = "f" * 32
    with pytest.raises(ValueError, match="trace/project"):
        _select(response, response.suggestions[0].suggestion_id)


def test_ambiguous_evidence_is_rejected() -> None:
    response = source()
    response.evidence.append(response.evidence[0])
    with pytest.raises(ValueError, match="ambiguous"):
        _select(response, response.suggestions[0].suggestion_id)


def test_approval_and_registered_functions_required(monkeypatch) -> None:
    runner = TrustedEvaluationRunner()
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="approval"):
        execute(
            runner=runner,
            change=DeveloperChange(
                approved=False, approved_by="developer", description="Not yet approved"
            ),
        )
    with pytest.raises(ValidationError):
        DeveloperChange(approved="true", approved_by="developer", description="bad consent")
    with pytest.raises(ValueError, match="distinct"):
        execute(before=baseline_agent, after=baseline_agent)
    with pytest.raises(ValueError, match="registered"):
        TrustedEvaluationRunner().require_distinct_agents("unknown", "another")
    with pytest.raises(ValidationError):
        ImplementationReference(
            registered_name="mock",
            implementation_id="v1",
            configuration_id="v1",
            source_code="print('untrusted')",
        )


@pytest.mark.parametrize(
    "before,after,expected",
    [
        (candidate_agent, baseline_agent, "regression"),
        (failed_agent, baseline_agent, "unchanged"),
    ],
)
def test_regression_and_failed_execution(before, after, expected) -> None:
    result = execute(before=before, after=after)
    assert result.classification == expected
    if before is failed_agent:
        assert result.comparison.baseline_run.results[0].error == "agent_error"


def test_unknown_and_historical_trace_reuse() -> None:
    assert execute(dataset=tasks(False)).classification == "inconclusive"
    result = execute(after=historical_agent)
    assert result.classification == "inconclusive"
    assert any("historical" in item for item in result.missing_evidence)


@pytest.mark.parametrize(
    "function,config,error",
    [
        (slow_agent, RunnerConfiguration(timeout_seconds=0.2), "timeout"),
        (excessive_agent, RunnerConfiguration(max_output_bytes=128), "invalid_output"),
    ],
)
def test_execution_limits(function, config, error) -> None:
    result = execute(after=function, config=config)
    assert result.comparison.candidate_run.results[0].error == error
    assert result.comparison.candidate_run.results[0].outcome == "failed"


def test_real_local_sdk_capture_diagnostics_recovery_and_artifacts(tmp_path) -> None:
    result = run_demo(tmp_path)
    assert result.classification == "improvement"
    before, after = result.comparison.baseline_run, result.comparison.candidate_run
    assert [r.outcome for r in before.results] == ["success", "failed"]
    assert [r.outcome for r in after.results] == ["success", "success"]
    assert before.results[1].observation.analysis.outcome.classification == "unrecovered_execution"
    assert after.results[1].observation.analysis.outcome.classification == "recovered_execution"
    assert result.comparison.metric_differences["tool_errors"] == 0
    assert result.comparison.metric_differences["success_rate"] == 0.5
    assert result.comparison.metric_differences["recovery_rate"] == 1
    assert result.comparison.metric_differences["input_tokens"] is None
    assert len(result.trace_ids) == 4 and result.source_trace_id not in result.trace_ids
    assert len(list(tmp_path.glob("*.json"))) == 7  # Five captured traces + two artifacts.
    saved = FixValidationResult.model_validate_json((tmp_path / "validation.json").read_bytes())
    assert saved == result
    source_response = json.loads((tmp_path / "source-suggestions.json").read_bytes())
    assert any(
        s["suggestion_id"] == result.suggestion.suggestion_id
        for s in source_response["suggestions"]
    )


@pytest.mark.parametrize("omit_candidate", [False, True])
def test_tradeoffs_and_missing_cases_with_runner_double(monkeypatch, omit_candidate) -> None:
    runner = TrustedEvaluationRunner()

    def fake_run(agent, dataset, *, run_id, configuration, provenance, **kwargs):
        results = [evaluate_case(c, "correct") for c in dataset.cases]
        for r in results:
            r.duration_ms = 10 if agent == "before" else 20
        if omit_candidate and agent == "after":
            results = []
        return make_run(
            dataset,
            results,
            run_id=run_id,
            agent=agent,
            mode="trusted_execution",
            configuration=configuration,
            provenance=provenance,
        )

    monkeypatch.setattr(runner, "run", fake_run)
    result = execute(runner=runner)
    if omit_candidate:
        assert result.classification == "inconclusive"
        assert result.comparison.cases[0].candidate is None
    else:
        assert result.classification == "unchanged"
        assert any("execution_duration_ms increased" in value for value in result.tradeoffs)
