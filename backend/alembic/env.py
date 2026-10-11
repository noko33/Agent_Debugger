from alembic import context

from app.core.config import get_settings
from app.core.database import make_engine
from app.models.traces import Base

config = context.config
target_metadata = Base.metadata


def run_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(get_settings())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    # Compile PostgreSQL DDL without requiring live credentials or an engine.
    context.configure(
        dialect_name="postgresql", target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run_online()
