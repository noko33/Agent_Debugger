import pytest
from pydantic import ValidationError

from app.schemas.diagnostics import DiagnosticParameters
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    RunnerConfiguration,
    ValidationCriterion,
)
from app.services.evaluation import aggregate, compare_runs, evaluate_case, make_run
from app.services.replay import AnalysisSnapshot, replay
from tests.diagnostic_fixtures import labeled_cases, root, span, trace


def case(identifier="one", expected="answer") -> EvaluationCase:
    return EvaluationCase(
        case_id=identifier,
        task="Return answer",
        criterion=ValidationCriterion(kind="exact_match", expected=expected),
    )


def run(cases, outputs, *, mode="trusted_execution", config=None, labels=None):
    dataset = EvaluationDataset(dataset_id="fixture", version="1", cases=cases)
    results = [
        evaluate_case(c, o, label=labels[i] if labels else None)
        for i, (c, o) in enumerate(zip(cases, outputs, strict=True))
    ]
    return make_run(
        dataset,
        results,
        run_id="run",
        agent="mock",
        mode=mode,
        configuration=config or RunnerConfiguration(),
        provenance={"revision": "fixture"},
    )


@pytest.mark.parametrize(
    "expected,output,outcome",
    [
        ("yes", "yes", "success"),
        ("yes", "YES", "failed"),
        (True, 1, "failed"),
        ({"a": 1}, {"a": 1, "b": 2}, "failed"),
        (None, None, "success"),
    ],
)
def test_exact_match(expected, output, outcome) -> None:
    assert evaluate_case(case(expected=expected), output).outcome == outcome


def test_structured_values_and_unknown() -> None:
    c = case()
    c.criterion = ValidationCriterion(kind="structured_values", expected={"nested": {"x": 1}})
    assert evaluate_case(c, {"nested": {"x": 1, "y": 2}}).outcome == "success"
    assert evaluate_case(c, {"nested": {"x": True}}).outcome == "failed"
    c.criterion = None
    observation = replay(
        AnalysisSnapshot.capture(trace(), [root()]), DiagnosticParameters()
    ).observation
    assert evaluate_case(c, "anything", observation=observation).outcome == "unknown"
    assert evaluate_case(c, label=True).outcome == "success"
    assert evaluate_case(c, label=False).outcome == "failed"
    with pytest.raises(ValueError):
        evaluate_case(c, label=1)


def test_dataset_and_run_validation() -> None:
    with pytest.raises(ValidationError):
        EvaluationDataset(dataset_id="x", version="1", cases=[case(), case()])
    with pytest.raises(ValidationError):
        ValidationCriterion(kind="structured_values", expected="not an object")
    artifact = run([case()], ["answer"])
    assert EvaluationRun.model_validate_json(artifact.model_dump_json()) == artifact
    with pytest.raises(ValidationError):
        EvaluationRun.model_validate({**artifact.model_dump(), "results": artifact.results * 2})
    with pytest.raises(ValueError):
        make_run(
            EvaluationDataset(dataset_id="x", version="1", cases=[case("other")]),
            artifact.results,
            run_id="bad",
            agent="mock",
            mode="analysis_only",
            configuration=RunnerConfiguration(),
            provenance={},
        )


@pytest.mark.parametrize(
    "before,after,direction",
    [
        (["bad"], ["answer"], "improvement"),
        (["answer"], ["bad"], "regression"),
        (["answer"], ["answer"], "unchanged"),
    ],
)
def test_matched_comparison(before, after, direction) -> None:
    report = compare_runs(run([case()], before), run([case()], after))
    assert report.direction == direction
    assert report.cases[0].direction == direction
    assert report.metric_differences["success_rate"] == (
        (1 if after[0] == "answer" else 0) - (1 if before[0] == "answer" else 0)
    )
    assert report.baseline_run.provenance == {"revision": "fixture"}


def test_mixed_unknown_missing_and_mismatched_cases() -> None:
    assert (
        compare_runs(
            run([case("a"), case("b")], ["bad", "answer"]),
            run([case("a"), case("b")], ["answer", "bad"]),
        ).direction
        == "inconclusive"
    )
    unknown = case()
    unknown.criterion = None
    assert compare_runs(run([unknown], [None]), run([unknown], [None])).direction == "inconclusive"
    report = compare_runs(run([case("a")], ["answer"]), run([case("b")], ["answer"]))
    assert len(report.cases) == 2 and report.direction == "inconclusive"
    assert all(value is None for value in report.metric_differences.values())
    changed = case()
    changed.inputs = {"changed": True}
    report = compare_runs(run([case()], ["answer"]), run([changed], ["answer"]))
    assert report.cases[0].reason == "Task, inputs or correctness criteria differ"


def test_replay_cannot_establish_repair_and_configs_must_match() -> None:
    before = run([case()], ["bad"], mode="analysis_only")
    after = run([case()], ["answer"], mode="analysis_only")
    assert compare_runs(before, after).direction == "inconclusive"
    different = run([case()], ["answer"], config=RunnerConfiguration(timeout_seconds=2))
    assert compare_runs(run([case()], ["bad"]), different).direction == "inconclusive"


def test_metrics_timing_tokens_failure_and_recovery() -> None:
    spans = [
        root(),
        span(
            2,
            operation="llm",
            start=0,
            end=800,
            attributes={"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 7},
        ),
        span(
            3,
            operation="llm",
            start=0,
            end=800,
            attributes={"gen_ai.usage.input_tokens": True, "gen_ai.usage.output_tokens": -1},
        ),
        span(4, status="error"),
    ]
    observation = replay(
        AnalysisSnapshot.capture(trace(), spans), DiagnosticParameters()
    ).observation
    r = evaluate_case(case(), "answer", observation=observation)
    metrics = {m.name: m for m in aggregate([r])}
    assert metrics["trace_wall_duration_ms"].value == 1000  # Not 2700 inclusive span ms.
    assert metrics["input_tokens"].value == 0 and metrics["input_tokens"].observed == 1
    assert metrics["input_tokens"].denominator == 2
    assert metrics["output_tokens"].value == 7
    assert metrics["tool_errors"].value == 1
    assert metrics["llm_errors"].value == 0
    assert metrics["recovery_rate"].value is None  # Later/root OK does not prove recovery.
    recovered = next(c for c in labeled_cases() if c.name == "explicit_recovery")
    failed = next(c for c in labeled_cases() if c.name == "failed_root")
    results = [
        evaluate_case(
            case(c.name),
            observation=replay(
                AnalysisSnapshot.capture(c.trace, c.spans), DiagnosticParameters()
            ).observation,
        )
        for c in (recovered, failed)
    ]
    metrics = {m.name: m for m in aggregate(results)}
    assert metrics["recovery_rate"].value == 0.5 and metrics["recovery_rate"].denominator == 2
    assert metrics["success_rate"].value is None  # No recorded output to validate.


def test_missing_metrics_and_incomplete_timing() -> None:
    r = evaluate_case(EvaluationCase(case_id="unknown", task="No criterion"))
    metrics = {m.name: m for m in aggregate([r])}
    assert metrics["success_rate"].value is None
    assert metrics["unknown_task_count"].value == 1
    assert metrics["input_tokens"].value is None
    assert metrics["trace_wall_duration_ms"].value is None
    assert metrics["tool_errors"].value is None
    r.observation = replay(
        AnalysisSnapshot.capture(trace(end_time=None), [root()]), DiagnosticParameters()
    ).observation
    metrics = {m.name: m for m in aggregate([r])}
    assert metrics["trace_wall_duration_ms"].value is None
    assert metrics["trace_coverage"].observed == 0


def test_partial_and_different_token_coverage_deltas_inconclusive() -> None:
    before, after = run([case()], ["answer"]), run([case()], ["answer"])
    for artifact, attributes in [(before, {"gen_ai.usage.input_tokens": 5}), (after, {})]:
        artifact.results[0].observation = replay(
            AnalysisSnapshot.capture(
                trace(), [root(), span(2, operation="llm", attributes=attributes)]
            ),
            DiagnosticParameters(),
        ).observation
    report = compare_runs(before, after)
    assert report.metric_differences["input_tokens"] is None


def test_observation_rule_configuration_must_match_run() -> None:
    c = case()
    result = evaluate_case(
        c,
        "answer",
        observation=replay(
            AnalysisSnapshot.capture(trace(), [root()]), DiagnosticParameters(slow_operation_ms=5)
        ).observation,
    )
    with pytest.raises(ValueError):
        make_run(
            EvaluationDataset(dataset_id="x", version="1", cases=[c]),
            [result],
            run_id="run",
            agent="mock",
            mode="analysis_only",
            provenance={},
            configuration=RunnerConfiguration(),
        )


def test_metric_rule_version_mismatch_not_comparable() -> None:
    before, after = run([case()], ["answer"]), run([case()], ["answer"])
    observed = replay(
        AnalysisSnapshot.capture(trace(), [root()]), DiagnosticParameters()
    ).observation
    before.results[0].observation = observed.model_copy(deep=True)
    after.results[0].observation = observed.model_copy(deep=True)
    after.results[0].observation.analysis.rules_version = "future"
    report = compare_runs(before, after)
    assert report.metric_differences["tool_errors"] is None
    assert report.metric_differences["success_rate"] == 0


def test_absent_output_is_unknown_but_explicit_null_can_be_validated() -> None:
    c = case(expected=None)
    assert evaluate_case(c).outcome == "unknown"
    assert evaluate_case(c).validation_basis == "none"
    assert evaluate_case(c, None).outcome == "success"
    assert evaluate_case(c, label=False).outcome == "failed"


def test_equal_counts_on_different_cases_do_not_make_trace_metrics_comparable() -> None:
    cases = [case("one"), case("two")]
    before, after = run(cases, ["answer", "answer"]), run(cases, ["answer", "answer"])
    observation = replay(
        AnalysisSnapshot.capture(trace(), [root()]), DiagnosticParameters()
    ).observation
    before.results[0].observation = observation
    after.results[1].observation = observation
    report = compare_runs(before, after)
    assert report.metric_differences["tool_errors"] is None
    assert report.metric_differences["success_rate"] == 0
