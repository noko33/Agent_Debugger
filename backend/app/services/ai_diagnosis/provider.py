"""Bounded OpenAI-compatible JSON-mode transport. No logs or raw errors escape."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.core.ai_config import AISettings
from app.services.ai_diagnosis.prompt import messages

if TYPE_CHECKING:
    import httpx


class ProviderFailure(Exception):
    def __init__(self, code: str, attempts: int = 0) -> None:
        self.code = code
        self.attempts = attempts
        super().__init__(code)


@dataclass
class ProviderResult:
    content: str
    attempts: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class AnalysisProvider(Protocol):
    def analyze(self, context: dict) -> ProviderResult: ...


class CompatibleProvider:
    def __init__(
        self, settings: AISettings, client_factory: Callable[..., "httpx.Client"] | None = None
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory

    def analyze(self, context: dict) -> ProviderResult:
        # Lazy import lets an existing development container keep serving disabled
        # analysis while its image is awaiting the updated runtime lockfile.
        import httpx

        config = self.settings
        if config.api_key is None or not config.api_key.get_secret_value().strip():
            raise ProviderFailure("provider_not_configured")
        payload = {
            "model": config.model,
            "messages": messages(context),
            "stream": False,
            "response_format": {"type": "json_object"},
            "max_tokens": config.max_output_tokens,
        }
        deadline = time.monotonic() + config.timeout_seconds
        factory = self.client_factory or httpx.Client
        with factory(follow_redirects=False) as client:
            for attempt in range(1, config.max_retries + 2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderFailure("provider_timeout", attempt - 1)
                try:
                    with client.stream(
                        "POST",
                        f"{config.base_url}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {config.api_key.get_secret_value()}"},
                        timeout=remaining,
                    ) as response:
                        if response.status_code in (429, 500, 502, 503, 504):
                            if attempt <= config.max_retries:
                                continue
                            raise ProviderFailure("provider_unavailable", attempt)
                        if response.status_code != 200:
                            raise ProviderFailure("provider_rejected", attempt)
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            if time.monotonic() >= deadline:
                                raise ProviderFailure("provider_timeout", attempt)
                            if len(body) + len(chunk) > config.max_response_bytes:
                                raise ProviderFailure("response_too_large", attempt)
                            body.extend(chunk)
                        return self._decode(bytes(body), attempt)
                except httpx.ConnectError:
                    if attempt > config.max_retries:
                        raise ProviderFailure("provider_unavailable", attempt) from None
                except httpx.TimeoutException:
                    # Ambiguous read/write timeouts are not retried to avoid duplicate billing.
                    raise ProviderFailure("provider_timeout", attempt) from None
                except httpx.HTTPError:
                    raise ProviderFailure("provider_unavailable", attempt) from None
        raise ProviderFailure("provider_unavailable", config.max_retries + 1)

    def _decode(self, body: bytes, attempt: int) -> ProviderResult:
        try:
            envelope = json.loads(body)
            choice = envelope["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise ProviderFailure("provider_refused", attempt)
            if choice.get("finish_reason") == "length":
                raise ProviderFailure("output_truncated", attempt)
            if choice.get("finish_reason") != "stop" or message.get("tool_calls"):
                raise ProviderFailure("invalid_provider_response", attempt)
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderFailure("invalid_provider_response", attempt)
            usage = envelope.get("usage") or {}
            counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
            if any(value is not None and (type(value) is not int or value < 0) for value in counts):
                raise ProviderFailure("invalid_provider_response", attempt)
            if counts[0] is not None and counts[0] > self.settings.max_input_tokens:
                raise ProviderFailure("reported_input_limit", attempt)
            if counts[1] is not None and counts[1] > self.settings.max_output_tokens:
                raise ProviderFailure("reported_output_limit", attempt)
            return ProviderResult(content, attempt, *counts)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ProviderFailure("invalid_provider_response", attempt) from None
