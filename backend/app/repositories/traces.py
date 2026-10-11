from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, selectinload

from app.models.traces import Project, Span, SpanEventRow, Trace
from app.schemas.traces import (
    ProjectCreate,
    ProjectRead,
    SpanCreate,
    SpanRead,
    StatusCode,
    TraceCreate,
    TraceRead,
)

SpanKey = tuple[str, str]


class TraceRepository(Protocol):
    def create_project(self, data: ProjectCreate) -> ProjectRead: ...
    def project_exists(self, project_id: UUID) -> bool: ...
    def create_trace(self, data: TraceCreate) -> TraceRead: ...
    def get_trace(self, trace_id: str) -> TraceRead | None: ...
    def list_traces(
        self, project_id: UUID | None, status: StatusCode | None, limit: int, offset: int
    ) -> tuple[list[TraceRead], int]: ...
    def lock_traces(self, trace_ids: list[str]) -> set[str]: ...
    def parent_links(self, trace_ids: list[str]) -> dict[SpanKey, str | None]: ...
    def existing_spans(self, keys: list[SpanKey]) -> dict[SpanKey, SpanRead]: ...
    def insert_spans(self, spans: list[SpanCreate]) -> None: ...
    def list_spans(self, trace_id: str, limit: int, offset: int) -> tuple[list[SpanRead], int]: ...


class SQLTraceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_project(self, data: ProjectCreate) -> ProjectRead:
        row = Project(**data.model_dump())
        self.session.add(row)
        self.session.flush()
        return ProjectRead.model_validate(row)

    def project_exists(self, project_id: UUID) -> bool:
        return self.session.get(Project, project_id) is not None

    def create_trace(self, data: TraceCreate) -> TraceRead:
        row = Trace(**data.model_dump())
        self.session.add(row)
        self.session.flush()
        return TraceRead.model_validate(row)

    def get_trace(self, trace_id: str) -> TraceRead | None:
        row = self.session.get(Trace, trace_id)
        return TraceRead.model_validate(row) if row is not None else None

    def list_traces(
        self, project_id: UUID | None, status: StatusCode | None, limit: int, offset: int
    ) -> tuple[list[TraceRead], int]:
        query = select(Trace)
        if project_id is not None:
            query = query.where(Trace.project_id == project_id)
        if status is not None:
            query = query.where(Trace.status_code == status)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.scalars(
            query.order_by(Trace.start_time.desc(), Trace.trace_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [TraceRead.model_validate(row) for row in rows], total

    def lock_traces(self, trace_ids: list[str]) -> set[str]:
        # A stable lock order prevents deadlocks across multi-trace batches.
        return set(
            self.session.scalars(
                select(Trace.trace_id)
                .where(Trace.trace_id.in_(trace_ids))
                .order_by(Trace.trace_id)
                .with_for_update()
            )
        )

    def parent_links(self, trace_ids: list[str]) -> dict[SpanKey, str | None]:
        rows = self.session.execute(
            select(Span.trace_id, Span.span_id, Span.parent_span_id).where(
                Span.trace_id.in_(trace_ids)
            )
        )
        return {(trace_id, span_id): parent for trace_id, span_id, parent in rows}

    def existing_spans(self, keys: list[SpanKey]) -> dict[SpanKey, SpanRead]:
        rows = self.session.scalars(
            select(Span)
            .where(tuple_(Span.trace_id, Span.span_id).in_(keys))
            .options(selectinload(Span.events))
        )
        return {(row.trace_id, row.span_id): SpanRead.model_validate(row) for row in rows}

    def insert_spans(self, spans: list[SpanCreate]) -> None:
        for data in spans:
            row = Span(**data.model_dump(exclude={"events"}))
            row.events = [
                SpanEventRow(position=position, **event.model_dump())
                for position, event in enumerate(data.events)
            ]
            self.session.add(row)
        self.session.flush()

    def list_spans(self, trace_id: str, limit: int, offset: int) -> tuple[list[SpanRead], int]:
        total = (
            self.session.scalar(
                select(func.count()).select_from(Span).where(Span.trace_id == trace_id)
            )
            or 0
        )
        rows = self.session.scalars(
            select(Span)
            .where(Span.trace_id == trace_id)
            .options(selectinload(Span.events))
            .order_by(Span.start_time, Span.span_id)
            .limit(limit)
            .offset(offset)
        )
        return [SpanRead.model_validate(row) for row in rows], total
