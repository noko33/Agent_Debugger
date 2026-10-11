from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.core.database import database_url
from app.models.traces import Span
from app.services.traces import reject_cycles


def test_offline_migration_generates_postgresql_ddl() -> None:
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    ddl = output.getvalue()
    for name in ["projects", "traces", "spans", "span_events"]:
        assert f"CREATE TABLE {name}" in ddl
    assert "JSONB" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "GENERATED ALWAYS AS" in ddl
    assert "FOREIGN KEY(trace_id, span_id)" in ddl
    assert "ck_span_parent" in ddl


def test_postgres_span_definition_has_composite_key() -> None:
    ddl = str(CreateTable(Span.__table__).compile(dialect=postgresql.dialect()))
    assert "PRIMARY KEY (trace_id, span_id)" in ddl
    assert "FOREIGN KEY(trace_id)" in ddl
    assert "FOREIGN KEY(parent_span_id)" not in ddl


def test_database_components_escape_passwords() -> None:
    settings = Settings(_env_file=None, postgres_password="@:/% sensitive")
    url = database_url(settings)
    assert url.password == "@:/% sensitive"
    assert "sensitive" not in str(url)
    assert "sensitive" not in repr(settings)


def test_long_parent_chain_does_not_recurse() -> None:
    links = {("trace", str(number)): str(number - 1) if number else None for number in range(5000)}
    reject_cycles(links)
