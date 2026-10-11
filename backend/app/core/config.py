from functools import lru_cache

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Agent Debugger"
    cors_origins: list[str] = ["http://localhost:3000"]
    database_url: PostgresDsn | None = Field(default=None, repr=False, exclude=True)
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "agent_debugger"
    postgres_db: str = "agent_debugger"
    postgres_password: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
