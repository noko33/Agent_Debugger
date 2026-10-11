import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test API")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3001"]')
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/agent_debugger")
    settings = Settings(_env_file=None)
    assert settings.app_name == "Test API"
    assert settings.cors_origins == ["http://localhost:3001"]
    assert settings.database_url is not None
    assert settings.database_url.scheme == "postgresql"


def test_database_url_requires_postgres() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///local.db")
