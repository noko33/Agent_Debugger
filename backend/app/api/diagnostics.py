from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.database import SessionDep
from app.core.errors import ErrorResponse
from app.repositories.diagnostics import SQLDiagnosticRepository
from app.schemas.diagnostics import DiagnosticResponse
from app.schemas.traces import TraceId
from app.services.diagnostic_service import DiagnosticService

router = APIRouter(
    prefix="/api/v1/traces",
    tags=["diagnostics"],
    responses={code: {"model": ErrorResponse} for code in (404, 413, 422, 503)},
)


def get_diagnostic_service(session: SessionDep, request: Request) -> DiagnosticService:
    return DiagnosticService(
        SQLDiagnosticRepository(session), request.app.state.diagnostic_parameters
    )


DiagnosticServiceDep = Annotated[DiagnosticService, Depends(get_diagnostic_service)]


@router.get("/{trace_id}/diagnostics", response_model=DiagnosticResponse)
def trace_diagnostics(
    trace_id: TraceId, service: DiagnosticServiceDep, project_id: UUID | None = None
) -> DiagnosticResponse:
    return service.analyze(trace_id, project_id)
