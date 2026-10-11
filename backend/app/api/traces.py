from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.database import SessionDep
from app.core.errors import ErrorResponse
from app.repositories.traces import SQLTraceRepository
from app.schemas.traces import (
    BatchResult,
    ProjectCreate,
    ProjectRead,
    SpanBatch,
    SpanPage,
    StatusCode,
    TraceCreate,
    TraceId,
    TracePage,
    TraceRead,
)
from app.services.traces import TraceService

router = APIRouter(
    prefix="/api/v1",
    tags=["traces"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 422, 503)},
)


def get_service(session: SessionDep) -> TraceService:
    return TraceService(SQLTraceRepository(session))


ServiceDep = Annotated[TraceService, Depends(get_service)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post("/projects", status_code=201, response_model=ProjectRead)
def create_project(data: ProjectCreate, service: ServiceDep) -> ProjectRead:
    return service.create_project(data)


@router.post("/traces", status_code=201, response_model=TraceRead)
def create_trace(data: TraceCreate, service: ServiceDep) -> TraceRead:
    return service.create_trace(data)


@router.post("/spans/batch", response_model=BatchResult)
def ingest_spans(data: SpanBatch, service: ServiceDep) -> BatchResult:
    return service.ingest(data)


@router.get("/traces", response_model=TracePage)
def list_traces(
    service: ServiceDep,
    project_id: UUID | None = None,
    status_code: StatusCode | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> TracePage:
    return service.list_traces(project_id, status_code, limit, offset)


@router.get("/traces/{trace_id}", response_model=TraceRead)
def get_trace(trace_id: TraceId, service: ServiceDep) -> TraceRead:
    return service.get_trace(trace_id)


@router.get("/traces/{trace_id}/spans", response_model=SpanPage)
def list_spans(
    trace_id: TraceId, service: ServiceDep, limit: Limit = 50, offset: Offset = 0
) -> SpanPage:
    return service.list_spans(trace_id, limit, offset)
