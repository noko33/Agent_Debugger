import time

import pytest

from app.schemas.diagnostics import DiagnosticParameters
from app.schemas.evaluation import (
    AgentOutput,
    EvaluationCase,
    EvaluationDataset,
    RunnerConfiguration,
    ValidationCriterion,
)
from app.services.evaluation import compare_runs
from app.services.evaluation_runner import TrustedEvaluationRunner
from app.services.replay import AnalysisSnapshot, replay
from tests.diagnostic_fixtures import root, trace


def mock_agent(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value=case.inputs["value"], trace_ids=[trace().trace_id])


async def async_agent(case: EvaluationCase) -> AgentOutput:
    return mock_agent(case)


def failed_agent(case: EvaluationCase) -> AgentOutput:
    raise RuntimeError("secret exception contents must not appear")


def slow_agent(case: EvaluationCase) -> AgentOutput:
    time.sleep(30)
    return mock_agent(case)


def excessive_agent(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value="x" * 1000)


def malformed_agent(case: EvaluationCase) -> AgentOutput:
    return {"unexpected": "secret"}


def multiple_traces(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value="answer", trace_ids=[trace().trace_id, "a" * 32])


def dataset(criterion=True) -> EvaluationDataset:
    return EvaluationDataset(
        dataset_id="runner-fixture",
        version="1",
        cases=[
            EvaluationCase(
                case_id="one",
                task="Return input",
                inputs={"value": "answer"},
                criterion=ValidationCriterion(kind="exact_match", expected="answer")
                if criterion
                else None,
            )
        ],
    )


@pytest.mark.parametrize("function", [mock_agent, async_agent])
def test_trusted_execution_reproducible_and_trace_resolution(function) -> None:
    runner = TrustedEvaluationRunner()
    runner.register("mock", function)
    observed = replay(
        AnalysisSnapshot.capture(trace(), [root()]), DiagnosticParameters()
    ).observation
    before = runner.run("mock", dataset(), run_id="before", resolve_trace=lambda *args: observed)
    after = runner.run("mock", dataset(), run_id="after", resolve_trace=lambda *args: observed)
    assert before.results[0].outcome == "success"
    assert before.results[0].trace_ids == [trace().trace_id]
    assert before.results[0].observation == observed
    assert before.results[0].duration_ms > 0
    assert before.provenance["process_start"] == "spawn"
    assert before.provenance["callable_source_digest"] != "unavailable"
    assert compare_runs(before, after).direction == "unchanged"
    observed.analysis.rules_version = "mutated"
    assert before.results[0].observation.analysis.rules_version == "4a.1"


@pytest.mark.parametrize(
    "function,error",
    [
        (failed_agent, "agent_error"),
        (excessive_agent, "invalid_output"),
        (malformed_agent, "invalid_output"),
    ],
)
def test_runner_errors_bounded_and_sanitized(function, error) -> None:
    runner = TrustedEvaluationRunner()
    runner.register("mock", function)
    artifact = runner.run(
        "mock", dataset(), run_id="error", configuration=RunnerConfiguration(max_output_bytes=128)
    )
    result = artifact.results[0]
    assert result.error == error and result.outcome == "failed"
    assert result.validation_basis == "execution_error"
    assert "secret" not in artifact.model_dump_json()


def test_runner_timeout_terminates_worker() -> None:
    runner = TrustedEvaluationRunner()
    runner.register("slow", slow_agent)
    started = time.monotonic()
    artifact = runner.run(
        "slow", dataset(), run_id="timeout", configuration=RunnerConfiguration(timeout_seconds=0.2)
    )
    assert artifact.results[0].error == "timeout"
    assert time.monotonic() - started < 5


def test_registration_and_unknown_outcome() -> None:
    runner = TrustedEvaluationRunner()
    with pytest.raises(ValueError):
        runner.run("unregistered", dataset(), run_id="no")
    with pytest.raises(ValueError):
        runner.register("lambda", lambda case: AgentOutput())
    runner.register("mock", mock_agent)
    with pytest.raises(ValueError):
        runner.register("mock", mock_agent)
    result = runner.run("mock", dataset(False), run_id="unknown").results[0]
    assert result.outcome == "unknown" and result.observation_issue == "unresolved"


def test_trace_resolution_failure_does_not_replace_outcome() -> None:
    def unavailable(*args):
        raise RuntimeError("secret database configuration")

    runner = TrustedEvaluationRunner()
    runner.register("mock", mock_agent)
    result = runner.run("mock", dataset(), run_id="unresolved", resolve_trace=unavailable)
    assert result.results[0].outcome == "success"
    assert result.results[0].observation_issue == "unresolved"
    assert "secret" not in result.model_dump_json()
    runner.register("many", multiple_traces)
    result = runner.run("many", dataset(), run_id="many").results[0]
    assert result.observation_issue == "multiple_traces" and result.observation is None
