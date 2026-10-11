from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

from app.api.health import router as health_router
from app.api.traces import router as trace_router
from app.core.config import Settings, get_settings
from app.core.database import make_engine
from app.core.errors import install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = None
        application.state.session_factory = None
        if config.database_url is not None or config.postgres_password is not None:
            engine = make_engine(config)
            application.state.session_factory = sessionmaker(engine, expire_on_commit=False)
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    application = FastAPI(title=config.app_name, version="0.2.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.include_router(health_router)
    application.include_router(trace_router)
    install_error_handlers(application)
    return application


app = create_app()
