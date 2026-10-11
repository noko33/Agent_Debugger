from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import APIError
from app.models.traces import Project, Span
from app.repositories.traces import SQLTraceRepository
from app.schemas.traces import ProjectCreate, SpanBatch, TraceCreate
from app.services.traces import TraceService
from tests.payloads import CHILD_ID, PARENT_ID, span_payload, trace_payload

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode", ["duplicate", "cycle"])
def test_concurrent_ingestion_serializes_per_trace(postgres_engine: Engine, mode: str) -> None:
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    trace_id = uuid4().hex
    with factory.begin() as session:
        service = TraceService(SQLTraceRepository(session))
        project = service.create_project(ProjectCreate(name="Concurrent test"))
        service.create_trace(
            TraceCreate.model_validate(trace_payload(str(project.id), trace_id=trace_id))
        )
    barrier = Barrier(2)

    def ingest(number: int) -> dict:
        payload = span_payload(trace_id=trace_id)
        if mode == "cycle":
            payload.update(
                span_id=PARENT_ID if number == 0 else CHILD_ID,
                parent_span_id=CHILD_ID if number == 0 else PARENT_ID,
            )
        barrier.wait(timeout=5)
        try:
            with factory.begin() as session:
                service = TraceService(SQLTraceRepository(session))
                return service.ingest(SpanBatch.model_validate({"spans": [payload]})).model_dump()
        except APIError as error:
            return {"error": error.code}

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(ingest, number) for number in range(2)]
            results = [future.result(timeout=15) for future in futures]
        if mode == "duplicate":
            assert sorted(result["inserted"] for result in results) == [0, 1]
            assert sorted(result["duplicates"] for result in results) == [0, 1]
        else:
            assert sum(result.get("error") == "span_cycle" for result in results) == 1
            assert sum(result.get("inserted", 0) for result in results) == 1
        with factory.begin() as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(Span).where(Span.trace_id == trace_id)
                )
                == 1
            )
    finally:
        with factory.begin() as session:
            session.execute(Project.__table__.delete().where(Project.id == project.id))
