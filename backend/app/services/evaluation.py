"""Deterministic validation, coverage-aware aggregation and comparable-case reports."""

from pydantic import JsonValue

from app.schemas.diagnostics import Category
from app.schemas.evaluation import (
    BaselineCandidateComparison,
    CaseComparison,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    MetricResult,
    RunnerConfiguration,
    TraceObservation,
)
from app.services.replay import canonical, digest


class _MissingOutput:
    pass


_MISSING = _MissingOutput()


def validate_output(case: EvaluationCase, output: JsonValue) -> str:
    """JSON type-sensitive equality: true is not 1. Structured values means recursive subset."""
    if case.criterion is None:
        return "unknown"

    def matches(expected: JsonValue, actual: JsonValue, subset: bool) -> bool:
        if subset and isinstance(expected, dict):
            return isinstance(actual, dict) and all(
                key in actual and matches(value, actual[key], True)
                for key, value in expected.items()
            )
        return canonical(expected) == canonical(actual)

    return (
        "success"
        if matches(case.criterion.expected, output, case.criterion.kind == "structured_values")
        else "failed"
    )


def evaluate_case(
    case: EvaluationCase,
    output: JsonValue | _MissingOutput = _MISSING,
    *,
    label: bool | None = None,
    observation: TraceObservation | None = None,
) -> EvaluationResult:
    """For offline results, an explicit observed success label may replace absent validation."""
    if label is not None and type(label) is not bool:
        raise ValueError("success label must be a boolean")
    missing = isinstance(output, _MissingOutput)
    recorded_output = None if missing else output
    outcome = "unknown" if missing else validate_output(case, recorded_output)
    basis = "validator" if case.criterion is not None and not missing else "none"
    if (case.criterion is None or missing) and label is not None:
        outcome, basis = ("success" if label else "failed"), "explicit_label"
    return EvaluationResult(
        case_id=case.case_id,
        case_digest=digest(case.model_dump(mode="json")),
        output=recorded_output,
        outcome=outcome,
        validation_basis=basis,
        observation=observation,
        trace_ids=[observation.analysis.trace_id] if observation is not None else [],
    )


def aggregate(results: list[EvaluationResult]) -> list[MetricResult]:
    metrics: list[MetricResult] = []
    count = len(results)

    def add(name, value, denominator, observed, unit, explanation):
        metrics.append(
            MetricResult(
                name=name,
                value=value,
                denominator=denominator,
                observed=observed,
                unit=unit,
                explanation=explanation,
            )
        )

    success = sum(r.outcome == "success" for r in results)
    failure = sum(r.outcome == "failed" for r in results)
    known = success + failure
    add("task_count", count, count, count, "count", "Number of evaluated cases")
    add(
        "success_rate",
        success / known if known else None,
        known,
        known,
        "fraction",
        "Success / known outcomes; unknown cases excluded, coverage shown separately",
    )
    add("known_task_count", known, count, known, "count", "Cases with a reliable outcome")
    add("failed_task_count", failure, count, count, "count", "Failed validation or execution")
    add("unknown_task_count", count - known, count, count, "count", "No reliable criterion")
    durations = [r.duration_ms for r in results if r.duration_ms is not None]
    add(
        "execution_duration_ms",
        sum(durations) if durations else None,
        count,
        len(durations),
        "ms",
        "Cumulative per-case runner wall time, not sum of spans or whole-run elapsed time",
    )
    observations = [r.observation for r in results if r.observation is not None]
    complete = [o for o in observations if o.complete]
    for key in ("tool_errors", "llm_errors"):
        add(
            key,
            sum(getattr(o, key) for o in observations) if observations else None,
            count,
            len(complete),
            "count",
            "Recorded failures; partial snapshots are lower bounds",
        )
    wall = [o.wall_duration_ms for o in observations if o.wall_duration_ms is not None]
    add(
        "trace_wall_duration_ms",
        sum(wall) if wall else None,
        count,
        len(wall),
        "ms",
        "Sum of recorded per-case Trace end-start intervals; overlapping spans never summed",
    )
    for key in ("input", "output"):
        values = [
            getattr(o, f"{key}_tokens")
            for o in observations
            if getattr(o, f"{key}_tokens") is not None
        ]
        add(
            f"{key}_tokens",
            sum(values) if values else None,
            sum(o.llm_span_count for o in observations),
            sum(getattr(o, f"{key}_token_coverage") for o in observations),
            "tokens",
            "Recorded LLM usage only; coverage is reporting spans / loaded LLM spans",
        )
    for category in Category:
        add(
            f"category.{category}",
            sum(any(f.category == category for f in o.analysis.findings) for o in observations)
            if observations
            else None,
            count,
            len(complete),
            "count",
            "Cases with category present, not number of findings; partial reads are lower bounds",
        )
    recovered = sum(o.analysis.outcome.classification == "recovered_execution" for o in complete)
    unrecovered = sum(
        o.analysis.outcome.classification == "unrecovered_execution" for o in complete
    )
    denominator = recovered + unrecovered
    add(
        "recovery_rate",
        recovered / denominator if denominator else None,
        denominator,
        denominator,
        "fraction",
        "Explicit recovered / (recovered + unrecovered) executions; "
        "unknown and failure-free executions excluded",
    )
    add(
        "recovery_coverage",
        denominator,
        count,
        denominator,
        "count",
        "Cases with explicit recovered/unrecovered execution evidence",
    )
    add(
        "trace_coverage",
        len(observations),
        count,
        len(complete),
        "count",
        "Attached trace observations; observed counts complete snapshots",
    )
    return metrics


def make_run(
    dataset: EvaluationDataset,
    results: list[EvaluationResult],
    *,
    run_id: str,
    agent: str,
    mode: str,
    configuration: RunnerConfiguration,
    provenance: dict[str, str],
) -> EvaluationRun:
    expected = {c.case_id: digest(c.model_dump(mode="json")) for c in dataset.cases}
    if any(expected.get(r.case_id) != r.case_digest for r in results):
        raise ValueError("result does not match the evaluation dataset")
    if any(
        r.observation is not None and r.observation.analysis.parameters != configuration.diagnostics
        for r in results
    ):
        raise ValueError("observation rule configuration differs from run configuration")
    # Detach caller-owned dictionaries and output objects before returning the artifact.
    return EvaluationRun.model_validate_json(
        EvaluationRun(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            agent=agent,
            mode=mode,
            configuration=configuration,
            provenance=provenance,
            results=results,
            metrics=aggregate(results),
        ).model_dump_json()
    )


def compare_runs(baseline: EvaluationRun, candidate: EvaluationRun) -> BaselineCandidateComparison:
    same_config = (
        baseline.schema_version == candidate.schema_version
        and baseline.dataset_id == candidate.dataset_id
        and baseline.dataset_version == candidate.dataset_version
        and baseline.configuration == candidate.configuration
        and baseline.mode == candidate.mode == "trusted_execution"
    )
    before, after = ({r.case_id: r for r in run.results} for run in (baseline, candidate))
    cases = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        direction, reason = "inconclusive", "Missing case or incompatible run configuration/mode"
        if old is not None and new is not None and same_config:
            if old.case_digest != new.case_digest:
                reason = "Task, inputs or correctness criteria differ"
            elif "unknown" in (old.outcome, new.outcome):
                reason = "Unknown semantic outcome"
            else:
                direction = (
                    "unchanged"
                    if old.outcome == new.outcome
                    else "improvement"
                    if new.outcome == "success"
                    else "regression"
                )
                reason = "Matched case and explicit observed outcomes"
        cases.append(
            CaseComparison(
                case_id=key,
                baseline=old.outcome if old else None,
                candidate=new.outcome if new else None,
                direction=direction,
                reason=reason,
            )
        )
    directions = {c.direction for c in cases}
    direction = "inconclusive"
    if directions and "inconclusive" not in directions:
        if directions <= {"unchanged", "improvement"}:
            direction = "improvement" if "improvement" in directions else "unchanged"
        elif directions <= {"unchanged", "regression"}:
            direction = "regression"
    metrics = ({m.name: m for m in aggregate(run.results)} for run in (baseline, candidate))
    old_metrics, new_metrics = metrics
    matched = (
        same_config
        and before.keys() == after.keys()
        and all(before[key].case_digest == after[key].case_digest for key in before)
    )
    differences = {}

    def diagnostic_versions(run: EvaluationRun) -> set[tuple[str, str]]:
        return {
            (r.observation.analysis.schema_version, r.observation.analysis.rules_version)
            for r in run.results
            if r.observation is not None
        }

    same_rules = diagnostic_versions(baseline) == diagnostic_versions(candidate)

    def coverage_signature(run: EvaluationRun, metric: str) -> list:
        """Equal aggregate counts can hide coverage on different tasks."""
        signatures = []
        for result in sorted(run.results, key=lambda r: r.case_id):
            if metric == "execution_duration_ms":
                signatures.append(result.duration_ms is not None)
                continue
            observation = result.observation
            signature = [observation is not None, observation.complete if observation else False]
            if observation is not None:
                if metric == "trace_wall_duration_ms":
                    signature.append(observation.wall_duration_ms is not None)
                elif metric in {"input_tokens", "output_tokens"}:
                    prefix = metric.split("_")[0]
                    signature.extend(
                        [
                            observation.llm_span_count,
                            getattr(observation, f"{prefix}_token_coverage"),
                        ]
                    )
                elif metric in {"recovery_rate", "recovery_coverage"}:
                    signature.append(
                        observation.analysis.outcome.classification
                        in {"recovered_execution", "unrecovered_execution"}
                    )
            signatures.append(signature)
        return signatures

    for key in sorted(old_metrics.keys() | new_metrics.keys()):
        a, b = old_metrics.get(key), new_metrics.get(key)
        comparable = (
            matched
            and a is not None
            and b is not None
            and a.denominator == b.denominator
            and a.observed == b.observed
            and a.value is not None
            and b.value is not None
            and (
                key
                in {
                    "task_count",
                    "success_rate",
                    "known_task_count",
                    "failed_task_count",
                    "unknown_task_count",
                }
                or coverage_signature(baseline, key) == coverage_signature(candidate, key)
            )
            and (
                same_rules
                or key
                in {
                    "task_count",
                    "success_rate",
                    "known_task_count",
                    "failed_task_count",
                    "unknown_task_count",
                    "execution_duration_ms",
                }
            )
        )
        differences[key] = b.value - a.value if comparable else None
    return BaselineCandidateComparison(
        baseline_run=baseline,
        candidate_run=candidate,
        cases=cases,
        metric_differences=differences,
        direction=direction,
        limitations=[
            "Descriptive matched-case comparison, not statistical or causal repair proof.",
            "Historical analysis replay alone cannot validate a repair.",
            "Metric deltas require matching cases, configuration and coverage.",
        ],
    )
