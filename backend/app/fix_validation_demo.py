"""Deterministic mock Agents and local SDK transport. No real LLM or database is used."""

import json
from pathlib import Path

import httpx
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
from app.schemas.fix_suggestions import FixRequest
from app.schemas.fix_validation import DeveloperChange, FixValidationResult, ImplementationReference
from app.schemas.traces import SpanRead, TraceRead
from app.services.ai_diagnosis.service import AIDiagnosisService, AnalysisGuard
from app.services.evaluation_runner import TrustedEvaluationRunner
from app.services.fix_suggestions import FixSuggestionService
from app.services.fix_validation import FixValidationService
from app.services.replay import AnalysisSnapshot, replay

PROJECT = "11111111-1111-4111-8111-111111111111"


def _execute(case: EvaluationCase, fallback_enabled: bool) -> AgentOutput:
    directory = Path(str(case.inputs["artifact_directory"]))
    directory.mkdir(parents=True, exist_ok=True)
    recorded = {}

    def transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/api/v1/traces":
            recorded["trace"] = TraceRead.model_validate(payload)
        elif request.url.path == "/api/v1/spans/batch":
            spans = [SpanRead.model_validate(s) for s in payload["spans"]]
            snapshot = AnalysisSnapshot.capture(recorded["trace"], spans)
            (directory / f"{recorded['trace'].trace_id}.json").write_bytes(snapshot.payload)
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={})

    value = None
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        exporter = APIExporter("http://fixture.local", PROJECT, client=http, max_retries=0)
        with AgentDebugger(PROJECT, exporter=exporter) as debugger:
            with debugger.trace("Catalog Agent", record_exception_details=False) as root:
                trace_id = format_trace_id(root.get_span_context().trace_id)
                with debugger.span("Plan action", operation_type="llm") as planning:
                    planning.set_status(Status(StatusCode.OK))
                try:
                    with debugger.span(
                        "Remote catalog", operation_type="tool", record_exception_details=False
                    ) as lookup:
                        if case.inputs["unavailable"]:
                            raise LookupError("deterministic fixture outage")
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
        if exporter.last_export_succeeded is not True:
            raise RuntimeError("Local fixture trace capture failed")
    return AgentOutput(value={"answer": value}, trace_ids=[trace_id])


def baseline_agent(case: EvaluationCase) -> AgentOutput:
    return _execute(case, fallback_enabled=False)


async def candidate_agent(case: EvaluationCase) -> AgentOutput:
    return _execute(case, fallback_enabled=True)


def dataset(directory: Path) -> EvaluationDataset:
    return EvaluationDataset(
        dataset_id="catalog-fallback",
        version="1",
        cases=[
            EvaluationCase(
                case_id=identifier,
                task="Return the catalog label",
                inputs={
                    "artifact_directory": str(directory.resolve()),
                    "unavailable": unavailable,
                    "item": item,
                },
                criterion=ValidationCriterion(
                    kind="structured_values", expected={"answer": item.upper()}
                ),
            )
            for identifier, unavailable, item in (
                ("available", False, "alpha"),
                ("outage", True, "beta"),
            )
        ],
    )


def run_demo(directory: Path) -> FixValidationResult:
    """Capture a source failure/suggestion, then execute fresh baseline and candidate traces."""
    runner = TrustedEvaluationRunner()
    runner.register("catalog-baseline", baseline_agent)
    runner.register("catalog-candidate", candidate_agent)
    tasks = dataset(directory)
    config = RunnerConfiguration(timeout_seconds=10)

    def resolve(trace_id: str, parameters: DiagnosticParameters) -> TraceObservation | None:
        return replay(
            AnalysisSnapshot((directory / f"{trace_id}.json").read_bytes()), parameters
        ).observation

    source = runner.run(
        "catalog-baseline",
        EvaluationDataset(dataset_id="source-outage", version="1", cases=[tasks.cases[1]]),
        run_id="source-capture",
        configuration=config,
        resolve_trace=resolve,
    )
    snapshot = json.loads((directory / f"{source.results[0].trace_ids[0]}.json").read_bytes())
    service = FixSuggestionService(
        AIDiagnosisService(AISettings(enabled=False), config.diagnostics, AnalysisGuard())
    )
    suggestions = service.analyze(
        (
            TraceRead.model_validate(snapshot["trace"]),
            [SpanRead.model_validate(s) for s in snapshot["spans"]],
        ),
        FixRequest(),
    )
    selected = next(s for s in suggestions.suggestions if s.category == "TOOL_POLICY")
    result = FixValidationService(runner).validate(
        suggestions,
        selected.suggestion_id,
        tasks,
        validation_id="catalog-fallback-validation",
        baseline=ImplementationReference(
            registered_name="catalog-baseline",
            implementation_id="catalog-v1",
            configuration_id="no-fallback",
        ),
        candidate=ImplementationReference(
            registered_name="catalog-candidate",
            implementation_id="catalog-v2",
            configuration_id="local-fallback",
        ),
        change=DeveloperChange(
            approved=True,
            approved_by="fixture-developer",
            description="Explicit mock candidate enables local catalog fallback after lookup error",
        ),
        configuration=config,
        resolve_trace=resolve,
        provenance={"fixture": "local catalog v1"},
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source-suggestions.json").write_text(
        suggestions.model_dump_json(indent=2), encoding="utf-8"
    )
    (directory / "validation.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
