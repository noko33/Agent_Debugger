from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import APIError
from app.models.traces import Span, Trace
from app.schemas.traces import SpanRead, TraceRead


class DiagnosticRepository(Protocol):
    def snapshot(
        self, trace_id: str, project_id: UUID | None, max_spans: int
    ) -> tuple[TraceRead, list[SpanRead]] | None: ...


class SQLDiagnosticRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def snapshot(
        self, trace_id: str, project_id: UUID | None, max_spans: int
    ) -> tuple[TraceRead, list[SpanRead]] | None:
        query = select(Trace).where(Trace.trace_id == trace_id)
        if project_id is not None:
            query = query.where(Trace.project_id == project_id)
        # SHARE conflicts with ingestion's trace FOR UPDATE lock. All span/event reads
        # therefore see one consistent stored graph, without modifying any rows.
        row = self.session.scalar(query.with_for_update(read=True))
        if row is None:
            return None
        count = (
            self.session.scalar(
                select(func.count()).select_from(Span).where(Span.trace_id == trace_id)
            )
            or 0
        )
        if count > max_spans:
            raise APIError(413, "diagnostic_span_limit", "Trace exceeds the diagnostic span limit")
        spans = self.session.scalars(
            select(Span)
            .where(Span.trace_id == trace_id)
            .options(selectinload(Span.events))
            .order_by(Span.start_time, Span.span_id)
        )
        try:
            return TraceRead.model_validate(row), [SpanRead.model_validate(span) for span in spans]
        except ValidationError as error:
            # Validation details can contain sensitive stored values; do not echo them.
            raise APIError(
                422, "invalid_stored_trace", "Stored trace violates the read contract"
            ) from error
