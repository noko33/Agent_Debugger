from app.schemas.diagnostics import Category, DiagnosticParameters, DiagnosticResponse
from app.schemas.traces import SpanRead, TraceRead
from app.services.diagnostics.outcome import classify_outcome
from app.services.diagnostics.rules import operation_findings, retry_findings
from app.services.diagnostics.signals import failed
from app.services.diagnostics.structure import structural_findings


def analyze_trace(
    trace: TraceRead, spans: list[SpanRead], parameters: DiagnosticParameters | None = None
) -> DiagnosticResponse:
    """Pure analysis of all stored records in a snapshot; never calls a provider."""
    parameters = parameters if parameters is not None else DiagnosticParameters()
    ordered = sorted(spans, key=lambda span: (span.start_time, span.span_id))
    structural = structural_findings(trace, ordered)
    findings = [
        *structural,
        *operation_findings(trace, ordered, parameters),
        *retry_findings(trace, ordered, parameters),
    ]
    findings.sort(key=lambda item: (item.rule_id, item.span_ids))
    return DiagnosticResponse(
        trace_id=trace.trace_id,
        project_id=trace.project_id,
        span_count=len(spans),
        operation_failure_count=sum(failed(span) for span in spans),
        repeated_failure_group_count=sum(
            item.category == Category.REPEATED_TOOL_FAILURE for item in findings
        ),
        parameters=parameters,
        outcome=classify_outcome(trace, ordered, bool(structural)),
        findings=findings,
    )
