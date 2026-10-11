from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import APIError


def database_url(settings: Settings) -> URL:
    if settings.database_url is not None:
        from sqlalchemy.engine import make_url

        url = make_url(str(settings.database_url))
        return url.set(drivername="postgresql+psycopg")
    if settings.postgres_password is None:
        raise ValueError("Set DATABASE_URL or POSTGRES_PASSWORD to configure PostgreSQL")
    # URL.create escapes credentials, including @, :, /, and % in local passwords.
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        hide_parameters=True,
    )


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    if factory is None:
        raise APIError(503, "database_unavailable", "Database is not configured")
    # Function-scoped dependency commits before FastAPI sends a success response.
    with factory.begin() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session, scope="function")]
