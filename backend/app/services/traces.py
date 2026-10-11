from uuid import UUID

from app.core.errors import APIError
from app.repositories.traces import SpanKey, TraceRepository
from app.schemas.traces import (
    BatchResult,
    ProjectCreate,
    ProjectRead,
    SpanBatch,
    SpanCreate,
    SpanPage,
    StatusCode,
    TraceCreate,
    TracePage,
    TraceRead,
)


def reject_cycles(links: dict[SpanKey, str | None]) -> None:
    visited: set[SpanKey] = set()
    for start in links:
        path: set[SpanKey] = set()
        current = start
        while current in links and current not in visited:
            if current in path:
                raise APIError(409, "span_cycle", "Span parents must form an acyclic graph")
            path.add(current)
            parent = links[current]
            if parent is None:
                break
            current = (current[0], parent)
        visited.update(path)


class TraceService:
    def __init__(self, repository: TraceRepository) -> None:
        self.repository = repository

    def create_project(self, data: ProjectCreate) -> ProjectRead:
        return self.repository.create_project(data)

    def create_trace(self, data: TraceCreate) -> TraceRead:
        if not self.repository.project_exists(data.project_id):
            raise APIError(404, "project_not_found", "Project does not exist")
        if self.repository.get_trace(data.trace_id) is not None:
            raise APIError(409, "trace_conflict", "Trace ID already exists")
        return self.repository.create_trace(data)

    def get_trace(self, trace_id: str) -> TraceRead:
        row = self.repository.get_trace(trace_id)
        if row is None:
            raise APIError(404, "trace_not_found", "Trace does not exist")
        return row

    def list_traces(
        self, project_id: UUID | None, status: StatusCode | None, limit: int, offset: int
    ) -> TracePage:
        items, total = self.repository.list_traces(project_id, status, limit, offset)
        return TracePage(items=items, total=total, limit=limit, offset=offset)

    def list_spans(self, trace_id: str, limit: int, offset: int) -> SpanPage:
        self.get_trace(trace_id)
        items, total = self.repository.list_spans(trace_id, limit, offset)
        return SpanPage(items=items, total=total, limit=limit, offset=offset)

    def ingest(self, batch: SpanBatch) -> BatchResult:
        trace_ids = sorted({span.trace_id for span in batch.spans})
        if self.repository.lock_traces(trace_ids) != set(trace_ids):
            raise APIError(404, "trace_not_found", "Every span must reference an existing trace")
        existing = self.repository.existing_spans(
            list({(span.trace_id, span.span_id) for span in batch.spans})
        )
        pending: dict[SpanKey, SpanCreate] = {}
        duplicates = 0
        for span in batch.spans:
            key = (span.trace_id, span.span_id)
            previous = pending.get(key) or existing.get(key)
            if previous is not None:
                if previous.model_dump(exclude={"duration_ms"}) != span.model_dump():
                    raise APIError(409, "span_conflict", "Span ID already has different content")
                duplicates += 1
            else:
                pending[key] = span
        links = self.repository.parent_links(trace_ids)
        links.update({key: span.parent_span_id for key, span in pending.items()})
        reject_cycles(links)
        self.repository.insert_spans(list(pending.values()))
        return BatchResult(received=len(batch.spans), inserted=len(pending), duplicates=duplicates)
