"""Final acceptance: spawned SDK Agents -> real TCP HTTP -> isolated PostgreSQL.

The reusable driver also supports the local development service for browser acceptance.
Only deterministic local functions are registered; no model provider is contacted.
"""

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from agent_debugger import AgentDebugger, APIExporter
from opentelemetry.trace import Status, StatusCode, format_trace_id

from app.core.ai_config import AISettings
from app.schemas.diagnostics import DiagnosticParameters
from app.schemas.evaluation import (
    AgentOutput,
    EvaluationCase,
    EvaluationDataset,
    RunnerConfiguration,
    TraceObservation,
    ValidationCriterion,
)
from app.schemas.fix_suggestions import FixResponse
from app.schemas.fix_validation import DeveloperChange, FixValidationResult, ImplementationReference
from app.schemas.traces import SpanRead, TraceRead
from app.services.evaluation_runner import TrustedEvaluationRunner
from app.services.fix_validation import FixValidationService
from app.services.replay import AnalysisSnapshot, replay
from tests.postgres.test_ai_diagnosis_http import live_ai as _live_ai

live_ai = _live_ai
pytestmark = pytest.mark.postgres


def execute_catalog(case: EvaluationCase, fallback_enabled: bool) -> AgentOutput:
    """Each worker exports its own new trace synchronously over real loopback HTTP."""
    project = str(case.inputs["project_id"])
    with httpx.Client(trust_env=False, timeout=5) as client:
        exporter = APIExporter(str(case.inputs["base_url"]), project, client=client, max_retries=0)
        with AgentDebugger(project, exporter=exporter) as debugger:
            with debugger.trace(
                "Final QA candidate" if fallback_enabled else "Final QA baseline"
            ) as root:
                trace_id = format_trace_id(root.get_span_context().trace_id)
                value = None
                with debugger.span("Plan action", operation_type="llm") as planning:
                    planning.set_status(Status(StatusCode.OK))
                try:
                    with debugger.span("Remote catalog", operation_type="tool") as lookup:
                        if case.inputs["unavailable"]:
                            raise LookupError("Local deterministic outage")
                        value = str(case.inputs["item"]).upper()
                        lookup.set_status(Status(StatusCode.OK))
                except LookupError:
                    if fallback_enabled:
                        with debugger.span(
                            "Local catalog fallback", operation_type="tool"
                        ) as fallback:
                            value = str(case.inputs["item"]).upper()
                            fallback.set_status(Status(StatusCode.OK))
                        root.add_event("recovery")
                    else:
                        root.set_status(Status(StatusCode.ERROR))
                with debugger.span("Final answer", operation_type="llm") as final:
                    final.set_status(Status(StatusCode.OK))
                if value is not None:
                    root.set_status(Status(StatusCode.OK))
        assert exporter.last_export_succeeded is True
    return AgentOutput(value={"answer": value}, trace_ids=[trace_id])


def http_baseline(case: EvaluationCase) -> AgentOutput:
    return execute_catalog(case, False)


async def http_candidate(case: EvaluationCase) -> AgentOutput:
    return execute_catalog(case, True)


def run_http_validation(
    client: httpx.Client, directory: Path, *, mock_ai: bool = False
) -> FixValidationResult:
    """Retain all five new executions and link the persisted records to the artifact."""
    response = client.post("/api/v1/projects", json={"name": "Final QA HTTP validation"})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    tasks = EvaluationDataset(
        dataset_id="final-qa-catalog",
        version="1",
        cases=[
            EvaluationCase(
                case_id=case_id,
                task="Return the catalog label",
                inputs={
                    "base_url": str(client.base_url),
                    "project_id": project_id,
                    "item": item,
                    "unavailable": unavailable,
                },
                criterion=ValidationCriterion(
                    kind="structured_values", expected={"answer": item.upper()}
                ),
            )
            for case_id, item, unavailable in (
                ("available", "alpha", False),
                ("outage", "beta", True),
            )
        ],
    )

    def snapshot(trace_id: str) -> AnalysisSnapshot:
        trace = client.get(f"/api/v1/traces/{trace_id}")
        spans = client.get(f"/api/v1/traces/{trace_id}/spans")
        assert trace.status_code == spans.status_code == 200
        return AnalysisSnapshot.capture(
            TraceRead.model_validate(trace.json()),
            [SpanRead.model_validate(s) for s in spans.json()["items"]],
        )

    def resolve(trace_id: str, parameters: DiagnosticParameters) -> TraceObservation:
        return replay(snapshot(trace_id), parameters).observation

    runner = TrustedEvaluationRunner()
    runner.register("http-baseline", http_baseline)
    runner.register("http-candidate", http_candidate)
    config = RunnerConfiguration(timeout_seconds=15)
    source = runner.run(
        "http-baseline",
        EvaluationDataset(dataset_id="qa-source", version="1", cases=[tasks.cases[1]]),
        run_id="source-capture",
        configuration=config,
        resolve_trace=resolve,
    )
    assert source.results[0].error is None, source.model_dump_json()
    source_id = source.results[0].trace_ids[0]
    path = f"/api/v1/traces/{source_id}"
    diagnostic = client.get(f"{path}/diagnostics")
    assert diagnostic.status_code == 200
    assert diagnostic.json()["outcome"]["classification"] == "unrecovered_execution"
    # Explicit consent is enabled only in the fixture with a replaced provider.
    ai = client.post(f"{path}/ai-diagnosis", json={"consent_to_external_processing": mock_ai})
    assert ai.status_code == 200
    assert ai.json()["execution"]["status"] in (
        {"completed"} if mock_ai else {"disabled", "consent_required"}
    )
    fix = client.post(f"{path}/fix-suggestions", json={})
    assert fix.status_code == 200
    suggestions = FixResponse.model_validate(fix.json())
    selected = next(s for s in suggestions.suggestions if s.category == "TOOL_POLICY")
    result = FixValidationService(runner).validate(
        suggestions,
        selected.suggestion_id,
        tasks,
        validation_id=f"final-qa-{uuid4().hex}",
        baseline=ImplementationReference(
            registered_name="http-baseline",
            implementation_id="http-catalog-v1",
            configuration_id="without-fallback",
        ),
        candidate=ImplementationReference(
            registered_name="http-candidate",
            implementation_id="http-catalog-v2",
            configuration_id="with-local-fallback",
        ),
        change=DeveloperChange(
            approved=True,
            approved_by="qa-developer",
            description="Explicit trusted candidate uses local fallback after catalog outage",
        ),
        configuration=config,
        resolve_trace=resolve,
        provenance={"acceptance_transport": "real-loopback-http-postgresql"},
    )
    assert len(set(result.trace_ids)) == 4 and source_id not in result.trace_ids
    baseline = result.comparison.baseline_run
    candidate = result.comparison.candidate_run
    assert [r.outcome for r in baseline.results] == ["success", "failed"]
    assert [r.outcome for r in candidate.results] == ["success", "success"]
    assert all(
        r.error is None and r.observation.complete for r in [*baseline.results, *candidate.results]
    )
    assert (
        baseline.results[1].observation.analysis.outcome.classification == "unrecovered_execution"
    )
    assert candidate.results[1].observation.analysis.outcome.classification == "recovered_execution"
    assert result.comparison.metric_differences["success_rate"] == 0.5
    assert result.comparison.metric_differences["tool_errors"] == 0
    assert result.comparison.metric_differences["input_tokens"] is None
    assert result.comparison.metric_differences["output_tokens"] is None
    # Classification concerns declared task criteria; timing trade-offs remain separate.
    directory.mkdir(parents=True, exist_ok=True)
    for trace_id in [source_id, *result.trace_ids]:
        captured = snapshot(trace_id)
        (directory / f"{trace_id}.json").write_bytes(captured.payload)
    (directory / "source-suggestions.json").write_text(
        suggestions.model_dump_json(indent=2), encoding="utf-8"
    )
    artifact = directory / "validation.json"
    artifact.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    assert FixValidationResult.model_validate_json(artifact.read_bytes()) == result
    return result


def test_spawned_fix_validation_real_http_postgresql(live_ai, tmp_path):
    live_ai.app.state.ai_settings = AISettings(enabled=True, cooldown_seconds=0)
    result = run_http_validation(live_ai.client, tmp_path, mock_ai=True)
    assert live_ai.provider.calls
    stored = live_ai.client.get(f"/api/v1/traces/{result.source_trace_id}/spans").json()["items"]
    assert any(e["name"] == "exception" for s in stored for e in s["events"])
    candidate = result.comparison.candidate_run.results[1]
    stored = live_ai.client.get(f"/api/v1/traces/{candidate.trace_ids[0]}/spans").json()["items"]
    assert any(e["name"] == "recovery" for s in stored for e in s["events"])
