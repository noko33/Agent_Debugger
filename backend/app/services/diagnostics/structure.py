from pydantic import JsonValue

from app.schemas.diagnostics import Category, Evidence, Finding, StrEnumNature
from app.schemas.traces import SpanRead, TraceRead
from app.services.diagnostics.rules import finding


def structural_findings(trace: TraceRead, spans: list[SpanRead]) -> list[Finding]:
    evidence: list[Evidence] = []
    related: set[str] = set()

    def issue(code: str, span: SpanRead | None = None, **values: JsonValue) -> None:
        evidence.append(
            Evidence(span_id=span.span_id if span else None, signal=code, values=values)
        )
        if span:
            related.add(span.span_id)

    if trace.end_time is None:
        issue("trace_end_missing", end_time=None)
    elif trace.end_time < trace.start_time:
        issue(
            "trace_times_reversed",
            start_time=trace.start_time.isoformat(),
            end_time=trace.end_time.isoformat(),
        )
    if not spans:
        issue("no_stored_spans", stored_span_count=0)
    by_id: dict[str, SpanRead] = {}
    for span in spans:
        if span.span_id in by_id:
            issue("duplicate_span_id", span)
        by_id[span.span_id] = span
        if span.trace_id != trace.trace_id:
            issue("span_trace_mismatch", span)
        if span.end_time is None:
            issue("span_end_missing", span, end_time=None)
        elif span.end_time < span.start_time:
            issue(
                "span_times_reversed",
                span,
                start_time=span.start_time.isoformat(),
                end_time=span.end_time.isoformat(),
            )
        if span.start_time < trace.start_time or (
            trace.end_time is not None and (span.end_time or span.start_time) > trace.end_time
        ):
            issue("span_outside_trace_bounds", span)
    for span in spans:
        parent = by_id.get(span.parent_span_id) if span.parent_span_id else None
        if span.parent_span_id and parent is None:
            issue("parent_missing", span, parent_span_id=span.parent_span_id)
        elif parent:
            if parent.span_id == span.span_id:
                issue("self_parent", span)
            if span.start_time < parent.start_time or (
                parent.end_time is not None and (span.end_time or span.start_time) > parent.end_time
            ):
                issue("span_outside_parent_bounds", span, parent_span_id=parent.span_id)
    done: set[str] = set()
    for start in sorted(by_id):
        current: str | None = start
        path: dict[str, int] = {}
        while current in by_id and current not in done:
            if current in path:
                cycle = list(path)[path[current] :]
                for member in cycle:
                    issue(
                        "parent_cycle", by_id[member], parent_span_id=by_id[member].parent_span_id
                    )
                break
            path[current] = len(path)
            current = by_id[current].parent_span_id
        done.update(path)
    roots = [s for s in spans if s.operation_type == "agent" and s.parent_span_id is None]
    if len(roots) == 1 and trace.status_code in ("ok", "error"):
        root = roots[0]
        if root.status_code in ("ok", "error") and root.status_code != trace.status_code:
            issue(
                "conflicting_root_trace_status",
                root,
                trace_status=trace.status_code,
                root_status=root.status_code,
            )
    if not evidence:
        return []
    return [
        finding(
            trace,
            Category.INCOMPLETE_OR_INVALID_TRACE,
            [span for span in spans if span.span_id in related],
            "Stored trace has incomplete or inconsistent data",
            "The evidence identifies timing or graph data-quality problems, not an Agent failure. "
            "Missing parents/spans can reflect delayed ingestion or sampling; timing bounds can "
            "reflect instrumentation or clock differences. No data is repaired or inferred.",
            evidence,
            nature=StrEnumNature.DATA_QUALITY,
        )
    ]
