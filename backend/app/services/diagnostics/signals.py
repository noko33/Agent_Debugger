from app.schemas.diagnostics import Evidence
from app.schemas.traces import SpanRead, StatusCode, TraceRead


def exception_signals(span: SpanRead) -> list[Evidence]:
    signals: list[Evidence] = []
    # Presence/type only: never echo exception messages, prompts or arbitrary attributes.
    if isinstance(span.attributes.get("error.type"), str) and span.attributes["error.type"]:
        signals.append(Evidence(span_id=span.span_id, signal="error.type_present"))
    for index, event in enumerate(span.events):
        if event.name == "exception" or (
            isinstance(event.attributes.get("exception.type"), str)
            and event.attributes["exception.type"]
        ):
            signals.append(
                Evidence(
                    span_id=span.span_id,
                    signal="exception_event",
                    values={"event_index": index, "timestamp": event.timestamp.isoformat()},
                )
            )
    return signals


def failed(span: SpanRead) -> bool:
    # An exception inside a final OK operation can have been handled locally.
    return span.status_code == StatusCode.ERROR or (
        span.status_code == StatusCode.UNSET and bool(exception_signals(span))
    )


def failure_evidence(span: SpanRead) -> list[Evidence]:
    return [
        Evidence(
            span_id=span.span_id,
            signal="operation_status",
            values={"status_code": span.status_code, "operation_type": span.operation_type},
        ),
        *exception_signals(span),
    ]


def duration_ms(record: TraceRead | SpanRead) -> float | None:
    if record.end_time is None or record.end_time < record.start_time:
        return None
    return (record.end_time - record.start_time).total_seconds() * 1000


def operation_identity(span: SpanRead) -> tuple[str, str]:
    key = "gen_ai.tool.name" if span.operation_type == "tool" else "gen_ai.request.model"
    value = span.attributes.get(key)
    if isinstance(value, str) and value.strip() and len(value) <= 200:
        # LLM model alone does not identify the operation; retain its recorded name.
        return (key, value) if span.operation_type == "tool" else ("span.name", span.name)
    return "span.name", span.name
