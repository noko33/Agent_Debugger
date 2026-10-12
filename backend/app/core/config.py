from functools import lru_cache

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.ai_config import AISettings
from app.schemas.diagnostics import DiagnosticParameters


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_nested_delimiter="__"
    )

    app_name: str = "Agent Debugger"
    cors_origins: list[str] = ["http://localhost:3000"]
    database_url: PostgresDsn | None = Field(default=None, repr=False, exclude=True)
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "agent_debugger"
    postgres_db: str = "agent_debugger"
    postgres_password: SecretStr | None = None
    diagnostics: DiagnosticParameters = Field(default_factory=DiagnosticParameters)
    ai: AISettings = Field(default_factory=AISettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
