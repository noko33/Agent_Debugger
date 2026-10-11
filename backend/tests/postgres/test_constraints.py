from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.traces import Base, Project, Span, SpanEventRow, Trace
from tests.payloads import PARENT_ID, TRACE_ID, span_payload, trace_payload

pytestmark = pytest.mark.postgres


@pytest.fixture
def stored_trace(api_client: TestClient, project_id: str) -> None:
    assert api_client.post("/api/v1/traces", json=trace_payload(project_id)).status_code == 201


@pytest.mark.usefixtures("stored_trace")
def test_unique_span_and_event_keys(
    api_client: TestClient, database_sessions: sessionmaker[Session]
) -> None:
    event = {"name": "state.changed", "timestamp": "2026-10-11T12:00:01Z"}
    payload = span_payload(events=[event, event])
    for _ in range(2):
        assert api_client.post("/api/v1/spans/batch", json={"spans": [payload]}).status_code == 200
    with database_sessions.begin() as session:
        assert len(session.scalars(select(Span)).all()) == 1
        assert len(session.scalars(select(SpanEventRow)).all()) == 2
        assert session.scalar(select(Span.duration_ms)) == 2000
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.add(
            Span(
                trace_id=TRACE_ID,
                span_id=PARENT_ID,
                name="duplicate",
                start_time=datetime.now(UTC),
                operation_type="runtime",
            )
        )
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.add(
            SpanEventRow(
                trace_id=TRACE_ID,
                span_id=PARENT_ID,
                position=0,
                name="duplicate",
                timestamp=datetime.now(UTC),
            )
        )


@pytest.mark.usefixtures("stored_trace")
@pytest.mark.parametrize(
    "overrides",
    [
        {"trace_id": "b" * 32},
        {"span_id": "0" * 16},
        {"parent_span_id": PARENT_ID},
        {"parent_span_id": "bad"},
        {"status_code": "bad"},
        {"operation_type": "invalid"},
        {"span_kind": "invalid"},
        {"end_time": datetime(2000, 1, 1, tzinfo=UTC)},
    ],
)
def test_span_database_constraints(
    database_sessions: sessionmaker[Session], overrides: dict
) -> None:
    data = {
        "trace_id": TRACE_ID,
        "span_id": PARENT_ID,
        "name": "raw SQL bypassing API",
        "start_time": datetime.now(UTC),
        "operation_type": "runtime",
        **overrides,
    }
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.execute(Span.__table__.insert().values(**data))


def test_trace_foreign_key(database_sessions: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.add(
            Trace(
                trace_id=TRACE_ID, project_id=uuid4(), name="orphan", start_time=datetime.now(UTC)
            )
        )


def test_event_foreign_key(database_sessions: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.add(
            SpanEventRow(
                trace_id=TRACE_ID,
                span_id=PARENT_ID,
                position=0,
                name="orphan",
                timestamp=datetime.now(UTC),
            )
        )


@pytest.mark.usefixtures("stored_trace")
def test_failed_write_rolls_back_all_rows(database_sessions: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.add(
            Span(
                trace_id=TRACE_ID,
                span_id=PARENT_ID,
                name="valid",
                start_time=datetime.now(UTC),
                operation_type="runtime",
            )
        )
        session.flush()
        session.add(
            Span(
                trace_id=TRACE_ID,
                span_id="0" * 16,
                name="invalid",
                start_time=datetime.now(UTC),
                operation_type="runtime",
            )
        )
    with database_sessions.begin() as session:
        assert session.scalar(select(Span.span_id)) is None


@pytest.mark.usefixtures("stored_trace")
def test_trace_primary_key(database_sessions: sessionmaker[Session], project_id: str) -> None:
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.add(
            Trace(
                trace_id=TRACE_ID,
                project_id=UUID(project_id),
                name="duplicate",
                start_time=datetime.now(UTC),
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"trace_id": "0" * 32},
        {"trace_id": "A" * 32},
        {"status_code": "bad"},
        {"end_time": datetime(2000, 1, 1, tzinfo=UTC)},
    ],
)
def test_trace_database_checks(
    database_sessions: sessionmaker[Session], project_id: str, overrides: dict
) -> None:
    data = {
        "trace_id": TRACE_ID,
        "project_id": UUID(project_id),
        "name": "raw trace",
        "start_time": datetime.now(UTC),
        **overrides,
    }
    with pytest.raises(IntegrityError), database_sessions.begin() as session:
        session.execute(Trace.__table__.insert().values(**data))


@pytest.mark.usefixtures("stored_trace")
def test_cascade_delete(api_client: TestClient, database_sessions: sessionmaker[Session]) -> None:
    payload = span_payload(events=[{"name": "event", "timestamp": "2026-10-11T12:00:01Z"}])
    api_client.post("/api/v1/spans/batch", json={"spans": [payload]})
    with database_sessions.begin() as session:
        session.execute(Project.__table__.delete())
        assert session.scalar(select(Trace.trace_id)) is None
        assert session.scalar(select(Span.span_id)) is None
        assert session.scalar(select(SpanEventRow.position)) is None


def test_migration_matches_models(database_sessions: sessionmaker[Session]) -> None:
    with database_sessions.begin() as session:
        context = MigrationContext.configure(session.connection(), opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_migration_roundtrip(database_sessions: sessionmaker[Session]) -> None:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    with database_sessions.begin() as session:
        connection = session.connection()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert "spans" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        assert {"projects", "traces", "spans", "span_events"} <= set(
            inspect(connection).get_table_names()
        )


def test_jsonb_roundtrip_and_microsecond_duration(
    api_client: TestClient, project_id: str, database_sessions: sessionmaker[Session]
) -> None:
    start = datetime(2026, 10, 11, tzinfo=UTC)
    attributes = {"nested": {"values": [1, "中文", None, True]}, "agent.name": "demo"}
    response = api_client.post(
        "/api/v1/traces",
        json=trace_payload(
            project_id,
            start_time=start.isoformat(),
            end_time=(start + timedelta(microseconds=123456)).isoformat(),
            attributes=attributes,
        ),
    )
    assert response.status_code == 201
    assert response.json()["attributes"] == attributes
    assert response.json()["duration_ms"] == pytest.approx(123.456)
    with database_sessions.begin() as session:
        assert session.scalar(select(Trace.duration_ms)) == pytest.approx(123.456)
