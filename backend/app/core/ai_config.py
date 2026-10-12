"""Optional, server-controlled provider and cost/privacy bounds."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    base_url: str = "https://api.deepseek.com"
    model: str = Field(default="deepseek-chat", pattern=r"^[a-zA-Z0-9._:/-]{1,100}$")
    timeout_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    max_retries: int = Field(default=1, ge=0, le=2)
    max_output_tokens: int = Field(default=1000, ge=128, le=4000)
    max_input_tokens: int = Field(default=16000, ge=4096, le=64000)
    max_context_bytes: int = Field(default=24000, ge=1024, le=64000)
    max_context_spans: int = Field(default=100, ge=1, le=500)
    max_context_findings: int = Field(default=50, ge=1, le=200)
    max_response_bytes: int = Field(default=65536, ge=1024, le=262144)
    cooldown_seconds: float = Field(default=30, ge=0, le=3600, allow_inf_nan=False)
    max_concurrent: int = Field(default=2, ge=1, le=4)

    @field_validator("base_url")
    @classmethod
    def safe_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Provider URL must be HTTPS without credentials, query or fragment")
        return value.rstrip("/")
