from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.traces import get_service
from app.core.config import Settings
from app.main import create_app
from app.services.traces import TraceService
from tests.fakes import MemoryRepository


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))
    service = TraceService(MemoryRepository())
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as client:
        yield client


@pytest.fixture
def project_id(api_client: TestClient) -> str:
    response = api_client.post("/api/v1/projects", json={"name": "Test agent"})
    assert response.status_code == 201
    return response.json()["id"]
