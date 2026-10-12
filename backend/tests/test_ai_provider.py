import json

import httpx
import pytest

from app.core.ai_config import AISettings
from app.services.ai_diagnosis.provider import CompatibleProvider, ProviderFailure


def envelope(**updates):
    result = {
        "choices": [{"finish_reason": "stop", "message": {"content": '{"hypotheses":[]}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    result.update(updates)
    return result


def provider(handler, **settings):
    config = AISettings(api_key="placeholder-unit-test-key", **settings)
    return CompatibleProvider(
        config,
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), trust_env=False, **kwargs
        ),
    )


def test_request_contract_and_reported_usage():
    def handle(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer placeholder-unit-test-key"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["stream"] is False and body["max_tokens"] == 1000
        assert body["messages"][0]["role"] == "system"
        assert "untrusted_trace_evidence" in body["messages"][1]["content"]
        assert 0 < request.extensions["timeout"]["read"] <= 10
        return httpx.Response(200, json=envelope())

    result = provider(handle).analyze({"facts": []})
    assert result.attempts == 1 and result.input_tokens == 100 and result.output_tokens == 50


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_retry_then_success(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status if len(calls) == 1 else 200, json=envelope())

    assert provider(handle).analyze({}).attempts == 2
    assert len(calls) == 2


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_retry_bound(retries):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503, text="private provider body")

    with pytest.raises(ProviderFailure) as caught:
        provider(handle, max_retries=retries).analyze({})
    assert caught.value.code == "provider_unavailable"
    assert caught.value.attempts == len(calls) == retries + 1
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "error,code,attempts",
    [
        (httpx.ConnectError, "provider_unavailable", 2),
        (httpx.ReadTimeout, "provider_timeout", 1),
        (httpx.WriteTimeout, "provider_timeout", 1),
    ],
)
def test_network_failure_safety(error, code, attempts):
    calls = []

    def handle(request):
        calls.append(request)
        raise error("secret provider error", request=request)

    with pytest.raises(ProviderFailure) as caught:
        provider(handle).analyze({})
    assert caught.value.code == code and caught.value.attempts == len(calls) == attempts
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("status", [401, 403, 400, 302])
def test_non_transient_failures_and_redirects_not_retried(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://other.test"})

    with pytest.raises(ProviderFailure, match="provider_rejected"):
        provider(handle).analyze({})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body,code",
    [
        ({}, "invalid_provider_response"),
        ({"choices": []}, "invalid_provider_response"),
        (envelope(usage={"prompt_tokens": True}), "invalid_provider_response"),
        (envelope(usage={"completion_tokens": -1}), "invalid_provider_response"),
        (envelope(usage={"prompt_tokens": 16001}), "reported_input_limit"),
        (envelope(usage={"completion_tokens": 1001}), "reported_output_limit"),
        (envelope(choices=[{"finish_reason": "length", "message": {}}]), "output_truncated"),
        (envelope(choices=[{"message": {"refusal": "private"}}]), "provider_refused"),
        (
            envelope(choices=[{"finish_reason": "tool_calls", "message": {}}]),
            "invalid_provider_response",
        ),
        (
            envelope(choices=[{"finish_reason": "stop", "message": {"content": ""}}]),
            "invalid_provider_response",
        ),
    ],
)
def test_invalid_envelopes(body, code):
    with pytest.raises(ProviderFailure, match=code):
        provider(lambda request: httpx.Response(200, json=body)).analyze({})


def test_missing_usage_is_not_estimated_and_bad_json_or_large_response_rejected():
    result = provider(lambda request: httpx.Response(200, json=envelope(usage=None))).analyze({})
    assert result.input_tokens is None and result.output_tokens is None
    for body, code in [
        (b"not JSON", "invalid_provider_response"),
        (b"x" * 1025, "response_too_large"),
    ]:
        with pytest.raises(ProviderFailure, match=code):
            provider(
                lambda request, body=body: httpx.Response(200, content=body),
                max_response_bytes=1024,
            ).analyze({})


def test_missing_key_never_creates_client():
    def forbidden(**kwargs):
        pytest.fail("Client must not be created without credentials")

    with pytest.raises(ProviderFailure, match="provider_not_configured"):
        CompatibleProvider(AISettings(), forbidden).analyze({})
