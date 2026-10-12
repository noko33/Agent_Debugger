"""Run from backend with PYTHONPATH set to that directory; see docs/EVALUATION.md."""

from app.schemas.evaluation import (
    AgentOutput,
    EvaluationCase,
    EvaluationDataset,
    ValidationCriterion,
)
from app.services.evaluation import compare_runs
from app.services.evaluation_runner import TrustedEvaluationRunner


def baseline(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value={"answer": case.inputs["left"] - case.inputs["right"]})


def candidate(case: EvaluationCase) -> AgentOutput:
    return AgentOutput(value={"answer": case.inputs["left"] + case.inputs["right"]})


def main() -> None:
    dataset = EvaluationDataset(
        dataset_id="arithmetic",
        version="1",
        cases=[
            EvaluationCase(
                case_id="add-2-3",
                task="Add two integers",
                inputs={"left": 2, "right": 3},
                criterion=ValidationCriterion(kind="structured_values", expected={"answer": 5}),
            )
        ],
    )
    runner = TrustedEvaluationRunner()
    runner.register("baseline", baseline)
    runner.register("candidate", candidate)
    before = runner.run(
        "baseline",
        dataset,
        run_id="baseline-1",
        provenance={"fixture": "deterministic arithmetic v1"},
    )
    after = runner.run(
        "candidate",
        dataset,
        run_id="candidate-1",
        provenance={"fixture": "deterministic arithmetic v1"},
    )
    report = compare_runs(before, after)
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    # Required for multiprocessing spawn on Windows.
    main()
