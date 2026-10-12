import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.diagnostics import SQLDiagnosticRepository
from app.repositories.traces import SQLTraceRepository
from app.schemas.diagnostics import Category
from app.schemas.traces import ProjectCreate, SpanBatch, TraceCreate
from app.services.traces import TraceService
from tests.diagnostic_fixtures import labeled_cases, root, span, trace

pytestmark = pytest.mark.postgres


def store_case(client: TestClient, case) -> tuple[str, str]:
    project = client.post("/api/v1/projects", json={"name": "Isolated diagnostics test"})
    assert project.status_code == 201
    project_id = project.json()["id"]
    trace_id = uuid4().hex
    payload = case.trace.model_dump(mode="json", exclude={"duration_ms"})
    payload.update(trace_id=trace_id, project_id=project_id)
    assert client.post("/api/v1/traces", json=payload).status_code == 201
    if case.spans:
        spans = [s.model_dump(mode="json", exclude={"duration_ms"}) for s in case.spans]
        for item in spans:
            item["trace_id"] = trace_id
        assert client.post("/api/v1/spans/batch", json={"spans": spans}).status_code == 200
    return trace_id, project_id


@pytest.mark.parametrize("case", labeled_cases(), ids=lambda case: case.name)
def test_persisted_diagnostic_labels(api_client: TestClient, case) -> None:
    trace_id, project_id = store_case(api_client, case)
    url = f"/api/v1/traces/{trace_id}/diagnostics"
    response = api_client.get(url, params={"project_id": project_id})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["trace_id"] == trace_id and result["project_id"] == project_id
    assert result["span_count"] == len(case.spans)
    assert {f["category"] for f in result["findings"]} == {c.value for c in case.expected}
    assert result["outcome"]["classification"] == case.outcome.value
    assert api_client.get(url).json() == result


def test_diagnostic_scope_and_missing_trace(api_client: TestClient) -> None:
    case = labeled_cases()[0]
    trace_id, _ = store_case(api_client, case)
    assert (
        api_client.get(
            f"/api/v1/traces/{trace_id}/diagnostics", params={"project_id": str(uuid4())}
        ).status_code
        == 404
    )
    assert api_client.get(f"/api/v1/traces/{uuid4().hex}/diagnostics").status_code == 404


def test_diagnostics_rejects_partial_analysis(api_client: TestClient) -> None:
    trace_id, _ = store_case(api_client, labeled_cases()[0])
    parameters = api_client.app.state.diagnostic_parameters
    api_client.app.state.diagnostic_parameters = parameters.model_copy(update={"max_spans": 1})
    try:
        response = api_client.get(f"/api/v1/traces/{trace_id}/diagnostics")
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "diagnostic_span_limit"
    finally:
        api_client.app.state.diagnostic_parameters = parameters


def test_structural_cycle_stored_outside_ingestion_is_diagnosed(
    api_client: TestClient, database_sessions: sessionmaker[Session]
) -> None:
    trace_id, _ = store_case(api_client, labeled_cases()[0])
    # DB permits references to missing parents; ingestion itself rejects cycles.
    with database_sessions.begin() as session:
        session.execute(
            text(
                "UPDATE spans SET parent_span_id = :parent "
                "WHERE trace_id = :trace_id AND span_id = :span"
            ),
            {
                "parent": span(2).span_id,
                "trace_id": trace_id,
                "span": root().span_id,
            },
        )
    result = api_client.get(f"/api/v1/traces/{trace_id}/diagnostics").json()
    quality = next(
        f for f in result["findings"] if f["category"] == Category.INCOMPLETE_OR_INVALID_TRACE
    )
    assert "parent_cycle" in {e["signal"] for e in quality["evidence"]}


def test_diagnostic_query_does_not_mutate_records(api_client: TestClient) -> None:
    trace_id, _ = store_case(api_client, labeled_cases()[11])
    before = api_client.get(f"/api/v1/traces/{trace_id}/spans?limit=500").json()
    assert api_client.get(f"/api/v1/traces/{trace_id}/diagnostics").status_code == 200
    assert api_client.get(f"/api/v1/traces/{trace_id}/spans?limit=500").json() == before


def test_invalid_stored_contract_returns_safe_422(
    api_client: TestClient, database_sessions: sessionmaker[Session]
) -> None:
    trace_id, _ = store_case(api_client, labeled_cases()[0])
    secret = "sensitive stored malformed content"
    with database_sessions.begin() as session:
        session.execute(
            text("UPDATE spans SET attributes = CAST(:value AS jsonb) WHERE trace_id = :trace_id"),
            {
                "value": json.dumps(secret),
                "trace_id": trace_id,
            },
        )
    response = api_client.get(f"/api/v1/traces/{trace_id}/diagnostics")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_stored_trace"
    assert secret not in response.text


def test_snapshot_lock_blocks_ingestion_until_read_finishes(postgres_engine: Engine) -> None:
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    trace_id = uuid4().hex
    with factory.begin() as session:
        service = TraceService(SQLTraceRepository(session))
        project = service.create_project(ProjectCreate(name="Isolated snapshot consistency test"))
        payload = trace().model_dump(exclude={"duration_ms"})
        payload.update(trace_id=trace_id, project_id=project.id)
        service.create_trace(TraceCreate.model_validate(payload))
        root_payload = root().model_dump(exclude={"duration_ms"})
        root_payload["trace_id"] = trace_id
        service.ingest(SpanBatch.model_validate({"spans": [root_payload]}))
    child_payload = span(2).model_dump(exclude={"duration_ms"})
    child_payload["trace_id"] = trace_id
    batch = SpanBatch.model_validate({"spans": [child_payload]})
    with factory.begin() as reader:
        snapshot = SQLDiagnosticRepository(reader).snapshot(trace_id, None, 5000)
        assert snapshot is not None and len(snapshot[1]) == 1
        with pytest.raises(OperationalError) as captured, factory.begin() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            TraceService(SQLTraceRepository(writer)).ingest(batch)
        assert captured.value.orig.sqlstate == "55P03"
    with factory.begin() as writer:
        assert TraceService(SQLTraceRepository(writer)).ingest(batch).inserted == 1
    with factory.begin() as reader:
        snapshot = SQLDiagnosticRepository(reader).snapshot(trace_id, None, 5000)
        assert snapshot is not None and len(snapshot[1]) == 2
