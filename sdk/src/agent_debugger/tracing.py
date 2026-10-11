"""Context-manager tracing using OpenTelemetry's context and ID generation."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Lock
from typing import Literal
from uuid import UUID

from opentelemetry import trace
from opentelemetry.context import Context, attach, create_key, detach, get_value, set_value
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.util.types import AttributeValue

from agent_debugger.exporter import OPERATION_ATTRIBUTE, APIExporter

logger = logging.getLogger(__name__)
Operation = Literal["agent", "llm", "tool", "runtime"]
_CLIENT_KEY = create_key("agent_debugger.client")


class _TraceProcessor(SpanProcessor):
    """Buffer only this client's active traces; export when their root ends."""

    def __init__(self, exporter: APIExporter, max_spans: int, max_traces: int) -> None:
        self.exporter = exporter
        self.max_spans = max_spans
        self.max_traces = max_traces
        self._pending: dict[int, list[ReadableSpan]] = {}
        self._started: dict[int, int] = {}
        self._lock = Lock()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        trace_id = span.get_span_context().trace_id
        with self._lock:
            if span.parent is None and len(self._pending) < self.max_traces:
                self._pending[trace_id] = []
                self._started[trace_id] = 0
            elif span.parent is None:
                self.exporter.last_export_succeeded = False
                logger.warning("Agent Debugger trace dropped: active trace limit reached")
            if trace_id in self._pending:
                self._started[trace_id] += 1
                if self._started[trace_id] > self.max_spans:
                    self._discard(trace_id)
                    self.exporter.last_export_succeeded = False
                    logger.warning("Agent Debugger trace dropped: span buffer limit reached")

    def _discard(self, trace_id: int) -> None:
        self._pending.pop(trace_id, None)
        self._started.pop(trace_id, None)

    def on_end(self, span: ReadableSpan) -> None:
        if span.context is None:
            return
        trace_id = span.context.trace_id
        with self._lock:
            pending = self._pending.get(trace_id)
            if pending is None:
                return
            pending.append(span)
            if span.parent is not None:
                return
            complete = len(pending) == self._started[trace_id]
            self._discard(trace_id)
        if complete:
            self.exporter.export(span, pending)
        else:
            self.exporter.last_export_succeeded = False
            logger.warning("Agent Debugger trace dropped: child spans outlived their root")

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        # Active traces cannot be sent because the ingestion API has immutable Trace records.
        with self._lock:
            return not self._pending

    def shutdown(self) -> None:
        with self._lock:
            if self._pending:
                self.exporter.last_export_succeeded = False
                logger.warning("Agent Debugger trace dropped: client closed with unfinished traces")
            self._pending.clear()
            self._started.clear()
        self.exporter.close()


class AgentDebugger:
    """Explicit tracing client. Finish nested work before leaving its trace context.

    Each trace starts a fresh root. Nested spans use OTel's current context, including
    asyncio task isolation. Threads require standard OTel context attach/detach.
    """

    def __init__(
        self,
        project_id: str | UUID,
        api_url: str = "http://localhost:8000",
        *,
        exporter: APIExporter | None = None,
        max_spans: int = 500,
        max_traces: int = 32,
    ) -> None:
        if not 1 <= max_spans <= 500 or not 1 <= max_traces <= 128:
            raise ValueError("max_spans must be 1..500 and max_traces 1..128")
        self.project_id = str(UUID(str(project_id)))
        self.exporter = exporter if exporter is not None else APIExporter(api_url, self.project_id)
        if self.exporter.project_id != self.project_id:
            raise ValueError("Exporter project_id must match client project_id")
        self._processor = _TraceProcessor(self.exporter, max_spans, max_traces)
        self._provider = TracerProvider(
            sampler=ALWAYS_ON,
            shutdown_on_exit=False,
            span_limits=SpanLimits(max_attributes=64, max_events=100, max_attribute_length=4096),
        )
        self._provider.add_span_processor(self._processor)
        self._tracer = self._provider.get_tracer("agent-debugger-sdk", "0.1.0")
        self._closed = False

    @contextmanager
    def trace(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        record_exception_details: bool = True,
    ) -> Iterator[trace.Span]:
        """Create a complete task trace and its root Agent span; export on exit."""
        self._check_name(name)
        # Use OTel's context API for scope ownership as well as span propagation.
        # No custom span stack, thread-local parent ID, or global provider is needed.
        token = attach(set_value(_CLIENT_KEY, self, Context()))
        try:
            with self._tracer.start_as_current_span(
                name.strip(),
                attributes={**(attributes or {}), OPERATION_ATTRIBUTE: "agent"},
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                try:
                    yield span
                except BaseException as exc:
                    _record_failure(span, exc, record_exception_details)
                    raise
        finally:
            detach(token)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        operation_type: Operation = "runtime",
        attributes: Mapping[str, AttributeValue] | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        record_exception_details: bool = True,
    ) -> Iterator[trace.Span]:
        """Create a child of the current OpenTelemetry span in an active SDK trace."""
        self._check_name(name)
        if operation_type not in {"agent", "llm", "tool", "runtime"}:
            raise ValueError("Unknown operation_type")
        # Dropped traces must remain harmless: their children keep valid OTel contexts,
        # but the bounded processor deliberately ignores their payloads.
        if get_value(_CLIENT_KEY) is not self:
            raise RuntimeError("Use span() inside trace()")
        with self._tracer.start_as_current_span(
            name.strip(),
            kind=kind,
            attributes={**(attributes or {}), OPERATION_ATTRIBUTE: operation_type},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException as exc:
                _record_failure(span, exc, record_exception_details)
                raise

    @property
    def is_tracing(self) -> bool:
        """Whether the current OTel context belongs to this open client."""
        return not self._closed and get_value(_CLIENT_KEY) is self

    def _check_name(self, name: str) -> None:
        if self._closed:
            raise RuntimeError("AgentDebugger is closed")
        if not name.strip() or len(name) > 200 or "\x00" in name:
            raise ValueError("Span name must contain 1..200 characters without NUL")

    def flush(self) -> bool:
        """True if no incomplete traces remain; completed traces export synchronously."""
        return self._processor.force_flush()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._provider.shutdown()

    def __enter__(self) -> AgentDebugger:
        if self._closed:
            raise RuntimeError("AgentDebugger is closed")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _record_failure(span: trace.Span, exc: BaseException, details: bool) -> None:
    # OTel's context manager handles Exception, but asyncio cancellation is BaseException.
    error_type = type(exc).__qualname__
    span.set_attribute("error.type", error_type)
    if details:
        span.record_exception(exc)
    else:
        span.add_event("exception", {"exception.type": error_type})
    span.set_status(Status(StatusCode.ERROR, str(exc) if details else None))
