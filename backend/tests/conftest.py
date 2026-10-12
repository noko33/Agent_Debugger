from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.ai_diagnosis import get_ai_snapshot
from app.api.diagnostics import get_diagnostic_service
from app.api.traces import get_service
from app.core.config import Settings
from app.core.errors import APIError
from app.main import create_app
from app.services.diagnostic_service import DiagnosticService
from app.services.traces import TraceService
from tests.fakes import MemoryRepository


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))
    service = TraceService(MemoryRepository())
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_diagnostic_service] = lambda: DiagnosticService(
        service.repository, app.state.diagnostic_parameters
    )

    def ai_snapshot(trace_id: str, project_id: UUID | None = None):
        snapshot = service.repository.snapshot(
            trace_id, project_id, app.state.diagnostic_parameters.max_spans
        )
        if snapshot is None:
            raise APIError(404, "trace_not_found", "Trace does not exist in the requested scope")
        return snapshot

    app.dependency_overrides[get_ai_snapshot] = ai_snapshot
    with TestClient(app) as client:
        yield client


@pytest.fixture
def project_id(api_client: TestClient) -> str:
    response = api_client.post("/api/v1/projects", json={"name": "Test agent"})
    assert response.status_code == 201
    return response.json()["id"]
