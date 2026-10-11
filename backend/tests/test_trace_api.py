from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.payloads import CHILD_ID, PARENT_ID, TRACE_ID, span_payload, trace_payload


def test_trace_roundtrip(api_client: TestClient, project_id: str) -> None:
    response = api_client.post("/api/v1/traces", json=trace_payload(project_id))
    assert response.status_code == 201
    assert response.json()["duration_ms"] == 2000
    fetched = api_client.get(f"/api/v1/traces/{TRACE_ID}")
    assert fetched.json() == response.json()
    assert api_client.post("/api/v1/traces", json=trace_payload(project_id)).status_code == 409


def test_missing_project_and_trace(api_client: TestClient) -> None:
    assert api_client.post("/api/v1/traces", json=trace_payload(str(uuid4()))).status_code == 404
    for suffix in ["", "/spans"]:
        assert api_client.get(f"/api/v1/traces/{TRACE_ID}{suffix}").status_code == 404
    response = api_client.post("/api/v1/spans/batch", json={"spans": [span_payload()]})
    assert response.status_code == 404


def test_validation_response_does_not_echo_payload(api_client: TestClient, project_id: str) -> None:
    response = api_client.post(
        "/api/v1/traces", json=trace_payload(project_id, trace_id="private-input-do-not-echo")
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-input" not in response.text
    assert api_client.get("/api/v1/traces/not-hex").status_code == 422


def test_trace_pagination_and_filters(api_client: TestClient, project_id: str) -> None:
    for number in range(1, 4):
        response = api_client.post(
            "/api/v1/traces",
            json=trace_payload(
                project_id, trace_id=f"{number:032x}", status_code="error" if number == 2 else "ok"
            ),
        )
        assert response.status_code == 201
    page = api_client.get("/api/v1/traces", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert page["items"][0]["trace_id"] == f"{2:032x}"
    filtered = api_client.get(
        "/api/v1/traces", params={"project_id": project_id, "status_code": "error"}
    ).json()
    assert filtered["total"] == 1
    empty = api_client.get("/api/v1/traces", params={"offset": 3}).json()
    assert empty["items"] == []
    assert empty["total"] == 3
    assert (
        api_client.get("/api/v1/traces", params={"project_id": str(uuid4())}).json()["total"] == 0
    )


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"status_code": "bad"}]
)
def test_invalid_pagination(api_client: TestClient, params: dict) -> None:
    assert api_client.get("/api/v1/traces", params=params).status_code == 422


def test_child_before_parent_and_retry(api_client: TestClient, project_id: str) -> None:
    api_client.post("/api/v1/traces", json=trace_payload(project_id))
    child = span_payload(
        span_id=CHILD_ID,
        parent_span_id=PARENT_ID,
        operation_type="tool",
        events=[
            {
                "name": "exception",
                "timestamp": "2026-10-11T12:00:01Z",
                "attributes": {
                    "exception.type": "ValueError",
                    "exception.message": "invalid tool input",
                },
            }
        ],
    )
    first = api_client.post("/api/v1/spans/batch", json={"spans": [child]})
    assert first.json() == {"received": 1, "inserted": 1, "duplicates": 0}
    retry = api_client.post("/api/v1/spans/batch", json={"spans": [span_payload(), child, child]})
    assert retry.json() == {"received": 3, "inserted": 1, "duplicates": 2}
    page = api_client.get(f"/api/v1/traces/{TRACE_ID}/spans").json()
    assert page["total"] == 2
    stored_child = next(row for row in page["items"] if row["span_id"] == CHILD_ID)
    assert stored_child["parent_span_id"] == PARENT_ID
    assert len(stored_child["events"]) == 1
    assert stored_child["duration_ms"] == 2000
    assert (
        len(api_client.get(f"/api/v1/traces/{TRACE_ID}/spans?limit=1&offset=1").json()["items"])
        == 1
    )


def test_duplicate_within_one_batch(api_client: TestClient, project_id: str) -> None:
    api_client.post("/api/v1/traces", json=trace_payload(project_id))
    response = api_client.post(
        "/api/v1/spans/batch", json={"spans": [span_payload(), span_payload()]}
    )
    assert response.json() == {"received": 2, "inserted": 1, "duplicates": 1}


def test_conflicting_retry_rolls_back_batch(api_client: TestClient, project_id: str) -> None:
    api_client.post("/api/v1/traces", json=trace_payload(project_id))
    api_client.post("/api/v1/spans/batch", json={"spans": [span_payload()]})
    response = api_client.post(
        "/api/v1/spans/batch",
        json={
            "spans": [
                span_payload(span_id=CHILD_ID),
                span_payload(name="changed"),
            ]
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "span_conflict"
    assert api_client.get(f"/api/v1/traces/{TRACE_ID}/spans").json()["total"] == 1


def test_cycles_rejected_across_batches(api_client: TestClient, project_id: str) -> None:
    api_client.post("/api/v1/traces", json=trace_payload(project_id))
    assert (
        api_client.post(
            "/api/v1/spans/batch", json={"spans": [span_payload(parent_span_id=CHILD_ID)]}
        ).status_code
        == 200
    )
    response = api_client.post(
        "/api/v1/spans/batch",
        json={"spans": [span_payload(span_id=CHILD_ID, parent_span_id=PARENT_ID)]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "span_cycle"
    assert api_client.get(f"/api/v1/traces/{TRACE_ID}/spans").json()["total"] == 1


def test_cycle_in_batch_is_atomic(api_client: TestClient, project_id: str) -> None:
    api_client.post("/api/v1/traces", json=trace_payload(project_id))
    response = api_client.post(
        "/api/v1/spans/batch",
        json={
            "spans": [
                span_payload(parent_span_id=CHILD_ID),
                span_payload(span_id=CHILD_ID, parent_span_id=PARENT_ID),
            ]
        },
    )
    assert response.status_code == 409
    assert api_client.get(f"/api/v1/traces/{TRACE_ID}/spans").json()["total"] == 0


def test_span_ids_are_scoped_to_trace(api_client: TestClient, project_id: str) -> None:
    other = "a" * 32
    for trace_id in [TRACE_ID, other]:
        api_client.post("/api/v1/traces", json=trace_payload(project_id, trace_id=trace_id))
    response = api_client.post(
        "/api/v1/spans/batch",
        json={"spans": [span_payload(), span_payload(trace_id=other, parent_span_id=CHILD_ID)]},
    )
    assert response.json()["inserted"] == 2


def test_unconfigured_database_health_still_works() -> None:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/api/v1/traces")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
