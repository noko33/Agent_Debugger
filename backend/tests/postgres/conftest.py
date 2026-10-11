import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("PostgreSQL integration blocked: TEST_DATABASE_URL is not configured")
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must refer to PostgreSQL, never SQLite")
    url = url.set(drivername="postgresql+psycopg")
    schema = "agent_debugger_test_" + uuid4().hex
    admin = create_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": f"-csearch_path={schema} -cstatement_timeout=10000",
        },
        hide_parameters=True,
    )
    try:
        config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        # Only the generated test schema is removed; existing application tables are untouched.
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def database_sessions(postgres_engine: Engine) -> Iterator[sessionmaker[Session]]:
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield sessionmaker(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
        finally:
            transaction.rollback()


@pytest.fixture
def api_client(database_sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, database_url=None, postgres_password=None))

    def test_session() -> Iterator[Session]:
        with database_sessions.begin() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    with TestClient(app) as client:
        yield client
