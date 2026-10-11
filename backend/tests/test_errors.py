from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api.traces import get_service
from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app


@pytest.mark.parametrize(
    "error, expected",
    [
        (IntegrityError("sql", {}, Exception("private database detail")), 409),
        (OperationalError("sql", {}, Exception("private database detail")), 503),
    ],
)
def test_database_failures_are_sanitized(error: Exception, expected: int) -> None:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))

    def unavailable_service() -> None:
        raise error

    app.dependency_overrides[get_service] = unavailable_service
    with TestClient(app) as client:
        response = client.get("/api/v1/traces")
        assert response.status_code == expected
        assert "private database detail" not in response.text


def test_transaction_failure_is_reported_before_success_response() -> None:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))

    class EmptySession:
        def scalar(self, query: object) -> int:
            return 0

        def scalars(self, query: object) -> list:
            return []

    def commit_failure() -> Iterator[Session]:
        yield EmptySession()  # type: ignore[misc]
        raise OperationalError("commit", {}, Exception("database failure at commit"))

    app.dependency_overrides[get_session] = commit_failure
    with TestClient(app) as client:
        response = client.get("/api/v1/traces")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
