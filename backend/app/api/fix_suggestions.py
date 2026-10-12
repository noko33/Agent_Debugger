from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.ai_diagnosis import get_ai_service, get_ai_snapshot
from app.core.errors import ErrorResponse
from app.schemas.fix_suggestions import FixRequest, FixResponse
from app.schemas.traces import SpanRead, TraceRead
from app.services.ai_diagnosis.service import AIDiagnosisService
from app.services.fix_suggestions import FixSuggestionService

router = APIRouter(
    prefix="/api/v1/traces",
    tags=["fix-suggestions"],
    responses={code: {"model": ErrorResponse} for code in (404, 413, 422, 503)},
)


@router.post("/{trace_id}/fix-suggestions", response_model=FixResponse)
def fix_suggestions(
    body: FixRequest,
    snapshot: Annotated[tuple[TraceRead, list[SpanRead]], Depends(get_ai_snapshot)],
    service: Annotated[AIDiagnosisService, Depends(get_ai_service)],
) -> FixResponse:
    # One optional hypothesis call; no second model request for recommendation wording.
    # Never accept caller-supplied "validated" analysis without server verification.
    return FixSuggestionService(service).analyze(snapshot, body)
