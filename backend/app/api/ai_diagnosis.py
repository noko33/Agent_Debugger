from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.database import SessionDep
from app.core.errors import APIError, ErrorResponse
from app.repositories.diagnostics import SQLDiagnosticRepository
from app.schemas.ai_diagnosis import AIDiagnosisResponse, AnalysisRequest
from app.schemas.traces import SpanRead, TraceId, TraceRead
from app.services.ai_diagnosis.service import AIDiagnosisService

router = APIRouter(
    prefix="/api/v1/traces",
    tags=["ai-diagnosis"],
    responses={code: {"model": ErrorResponse} for code in (404, 413, 422, 503)},
)


def get_ai_snapshot(
    trace_id: TraceId, session: SessionDep, request: Request, project_id: UUID | None = None
) -> tuple[TraceRead, list[SpanRead]]:
    snapshot = SQLDiagnosticRepository(session).snapshot(
        trace_id, project_id, request.app.state.diagnostic_parameters.max_spans
    )
    if snapshot is None:
        raise APIError(404, "trace_not_found", "Trace does not exist in the requested scope")
    # Finish this read-only transaction BEFORE any potentially slow external request.
    # Snapshot objects are detached Pydantic records; no DB lock spans model execution.
    session.commit()
    return snapshot


def get_ai_service(request: Request) -> AIDiagnosisService:
    return AIDiagnosisService(
        request.app.state.ai_settings,
        request.app.state.diagnostic_parameters,
        request.app.state.ai_guard,
    )


@router.post("/{trace_id}/ai-diagnosis", response_model=AIDiagnosisResponse)
def ai_diagnosis(
    body: AnalysisRequest,
    snapshot: Annotated[tuple[TraceRead, list[SpanRead]], Depends(get_ai_snapshot)],
    service: Annotated[AIDiagnosisService, Depends(get_ai_service)],
) -> AIDiagnosisResponse:
    return service.analyze(snapshot, body.consent_to_external_processing)
