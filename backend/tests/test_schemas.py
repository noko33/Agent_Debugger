from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.traces import SpanBatch, SpanCreate, TraceCreate, TraceRead
from tests.payloads import PARENT_ID, span_payload, trace_payload


@pytest.mark.parametrize("trace_id", ["0" * 32, "a" * 31, "A" * 32, "g" * 32, 42])
def test_invalid_trace_ids(trace_id: object) -> None:
    with pytest.raises(ValidationError):
        TraceCreate.model_validate(trace_payload(str(uuid4()), trace_id=trace_id))


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_time": "2026-10-11T12:00:00"},
        {"end_time": "2026-10-11T11:59:59Z"},
        {"status_code": "success"},
        {"name": "   "},
        {"project_id": "not-a-uuid"},
        {"unknown_field": "unexpected"},
        {"attributes": {"cost": float("nan")}},
        {"attributes": {"large": "x" * 33_000}},
        {"name": "bad\x00name"},
        {"attributes": {"nested": [{"text": "bad\x00value"}]}},
        {"attributes": {"bad\x00key": "value"}},
        {"attributes": {"text": "\ud800"}},
    ],
)
def test_invalid_trace_payloads(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        TraceCreate.model_validate(trace_payload(str(uuid4()), **overrides))


def test_timezone_normalization_and_duration() -> None:
    trace = TraceRead.model_validate(
        trace_payload(
            str(uuid4()),
            start_time="2026-10-11T20:00:00+08:00",
            end_time="2026-10-11T12:00:00.123456Z",
        )
    )
    assert trace.start_time.isoformat() == "2026-10-11T12:00:00+00:00"
    assert trace.duration_ms == pytest.approx(123.456)
    assert TraceRead.model_validate(trace_payload(str(uuid4()), end_time=None)).duration_ms is None


def test_response_json_roundtrip_preserves_derived_duration() -> None:
    trace = TraceRead.model_validate(trace_payload(str(uuid4())))
    assert TraceRead.model_validate_json(trace.model_dump_json()) == trace
    data = trace.model_dump()
    data["duration_ms"] = -1
    assert TraceRead.model_validate(data).duration_ms == 2000


@pytest.mark.parametrize(
    "overrides",
    [
        {"span_id": "0" * 16},
        {"span_id": "a" * 15},
        {"parent_span_id": PARENT_ID},
        {"parent_span_id": "0" * 16},
        {"operation_type": "chatbot"},
        {"span_kind": "unknown"},
        {"events": [{"name": "exception", "timestamp": "2026-10-11T12:00:00"}]},
        {"events": [{"name": "event", "timestamp": "2026-10-11T12:00:00Z"}] * 101},
    ],
)
def test_invalid_span_payloads(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        SpanCreate.model_validate(span_payload(**overrides))


@pytest.mark.parametrize("spans", [[], [span_payload()] * 501])
def test_batch_count_bounds(spans: list) -> None:
    with pytest.raises(ValidationError):
        SpanBatch.model_validate({"spans": spans})


def test_batch_byte_bound() -> None:
    span = span_payload(attributes={"data": "x" * 30_000})
    with pytest.raises(ValidationError, match="1 MiB"):
        SpanBatch.model_validate({"spans": [span] * 40})
