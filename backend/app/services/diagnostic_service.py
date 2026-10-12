from uuid import UUID

from app.core.errors import APIError
from app.repositories.diagnostics import DiagnosticRepository
from app.schemas.diagnostics import DiagnosticParameters, DiagnosticResponse
from app.services.diagnostics import analyze_trace


class DiagnosticService:
    def __init__(self, repository: DiagnosticRepository, parameters: DiagnosticParameters) -> None:
        self.repository = repository
        self.parameters = parameters

    def analyze(self, trace_id: str, project_id: UUID | None = None) -> DiagnosticResponse:
        snapshot = self.repository.snapshot(trace_id, project_id, self.parameters.max_spans)
        if snapshot is None:
            raise APIError(404, "trace_not_found", "Trace does not exist in the requested scope")
        return analyze_trace(*snapshot, self.parameters)
