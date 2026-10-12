from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import APIError
from app.repositories.traces import SpanKey
from app.schemas.traces import (
    ProjectCreate,
    ProjectRead,
    SpanCreate,
    SpanRead,
    StatusCode,
    TraceCreate,
    TraceRead,
)


class MemoryRepository:
    """Service test double, not a persistence implementation or database substitute."""

    def __init__(self) -> None:
        self.projects: dict[UUID, ProjectRead] = {}
        self.traces: dict[str, TraceRead] = {}
        self.spans: dict[SpanKey, SpanRead] = {}

    def create_project(self, data: ProjectCreate) -> ProjectRead:
        row = ProjectRead(**data.model_dump(), id=uuid4(), created_at=datetime.now(UTC))
        self.projects[row.id] = row
        return row

    def project_exists(self, project_id: UUID) -> bool:
        return project_id in self.projects

    def create_trace(self, data: TraceCreate) -> TraceRead:
        row = TraceRead(**data.model_dump())
        self.traces[row.trace_id] = row
        return row

    def get_trace(self, trace_id: str) -> TraceRead | None:
        return self.traces.get(trace_id)

    def list_traces(
        self, project_id: UUID | None, status: StatusCode | None, limit: int, offset: int
    ) -> tuple[list[TraceRead], int]:
        rows = [
            row
            for row in self.traces.values()
            if (project_id is None or row.project_id == project_id)
            and (status is None or row.status_code == status)
        ]
        rows.sort(key=lambda row: (row.start_time, row.trace_id), reverse=True)
        return rows[offset : offset + limit], len(rows)

    def snapshot(
        self, trace_id: str, project_id: UUID | None, max_spans: int
    ) -> tuple[TraceRead, list[SpanRead]] | None:
        trace = self.get_trace(trace_id)
        if trace is None or (project_id is not None and trace.project_id != project_id):
            return None
        spans, count = self.list_spans(trace_id, max_spans + 1, 0)
        if count > max_spans:
            raise APIError(413, "diagnostic_span_limit", "Trace exceeds the diagnostic span limit")
        return trace, spans

    def lock_traces(self, trace_ids: list[str]) -> set[str]:
        return set(trace_ids) & self.traces.keys()

    def parent_links(self, trace_ids: list[str]) -> dict[SpanKey, str | None]:
        return {key: row.parent_span_id for key, row in self.spans.items() if key[0] in trace_ids}

    def existing_spans(self, keys: list[SpanKey]) -> dict[SpanKey, SpanRead]:
        return {key: self.spans[key] for key in keys if key in self.spans}

    def insert_spans(self, spans: list[SpanCreate]) -> None:
        for span in spans:
            self.spans[(span.trace_id, span.span_id)] = SpanRead(**span.model_dump())

    def list_spans(self, trace_id: str, limit: int, offset: int) -> tuple[list[SpanRead], int]:
        rows = sorted(
            (span for span in self.spans.values() if span.trace_id == trace_id),
            key=lambda row: (row.start_time, row.span_id),
        )
        return rows[offset : offset + limit], len(rows)
