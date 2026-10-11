"""Translate finished OpenTelemetry spans into the existing v1 ingestion contract."""

import json
import logging
import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import format_span_id, format_trace_id

logger = logging.getLogger(__name__)
Payload = dict[str, Any]
OPERATION_ATTRIBUTE = "agent_debugger.operation_type"


def timestamp(nanoseconds: int) -> str:
    # Avoid floating-point rounding when converting OTel nanoseconds to API microseconds.
    seconds, remainder = divmod(nanoseconds, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=remainder // 1000).isoformat()


def timed_payload(span: ReadableSpan) -> Payload:
    if span.start_time is None or span.end_time is None:
        raise ValueError("Only completed spans can be exported")
    attributes = dict(span.attributes or {})
    attributes.pop(OPERATION_ATTRIBUTE, None)
    return {
        "name": span.name,
        "start_time": timestamp(span.start_time),
        "end_time": timestamp(span.end_time),
        "status_code": span.status.status_code.name.lower(),
        "status_message": span.status.description,
        "attributes": attributes,
    }


class APIExporter:
    """Synchronous best-effort exporter with bounded retries and no background worker.

    A supplied HTTP client remains owned by the caller. No global OTel provider is changed.
    Failure logs deliberately exclude URLs, response bodies, exceptions, and payloads.
    """

    def __init__(
        self,
        api_url: str,
        project_id: str | UUID,
        *,
        timeout: float = 2.0,
        max_retries: int = 1,
        redact: Callable[[Payload], Payload] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = httpx.URL(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("api_url must be an HTTP(S) URL")
        if parsed.userinfo or parsed.query or parsed.fragment:
            raise ValueError("api_url must not contain credentials, query, or fragment")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if not 0 <= max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        self.api_url = api_url.rstrip("/")
        self.project_id = str(UUID(str(project_id)))
        self.timeout = timeout
        self.max_retries = max_retries
        self.redact = redact
        self._owns_client = client is None
        # Local development must not accidentally send trace contents through env proxies.
        self._client = client if client is not None else httpx.Client(trust_env=False)
        self.last_export_succeeded: bool | None = None

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method, self.api_url + path, timeout=self.timeout, **kwargs
                )
            except httpx.RequestError:
                if attempt == self.max_retries:
                    raise
                continue
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                return response
            if attempt == self.max_retries:
                return response
        raise RuntimeError("unreachable")

    def _prepare(self, payload: Payload) -> Payload:
        if self.redact is not None:
            payload = self.redact(payload)
        # Validate JSON and size before sending any part of a trace. This is wire encoding,
        # not a second schema implementation; backend schemas remain authoritative.
        payload = json.loads(json.dumps(payload, allow_nan=False, ensure_ascii=False))
        for attributes in [
            payload["attributes"],
            *[event["attributes"] for event in payload.get("events", [])],
        ]:
            if len(json.dumps(attributes, ensure_ascii=False).encode("utf-8")) > 32_768:
                raise ValueError("Attributes exceed ingestion limit")
        return payload

    @staticmethod
    def _same_trace(stored: Payload, submitted: Payload) -> bool:
        # A POST response lost after commit may make its retry return 409. Proceed only
        # after confirming that the stored trace is the exact same immutable record.
        for key, value in submitted.items():
            actual = stored.get(key)
            if key in {"start_time", "end_time"}:
                if actual is None or datetime.fromisoformat(actual) != datetime.fromisoformat(
                    value
                ):
                    return False
            elif actual != value:
                return False
        return True

    def export(self, root: ReadableSpan, spans: Sequence[ReadableSpan]) -> bool:
        """Export one completed trace. Never raise an API/serialization/redaction error."""
        try:
            if root.context is None:
                raise ValueError("Missing trace context")
            trace_id = format_trace_id(root.context.trace_id)
            trace = self._prepare(
                {
                    **timed_payload(root),
                    "trace_id": trace_id,
                    "project_id": self.project_id,
                }
            )
            rows = []
            for span in spans:
                if span.context is None or span.context.trace_id != root.context.trace_id:
                    raise ValueError("Span does not belong to trace")
                rows.append(
                    self._prepare(
                        {
                            **timed_payload(span),
                            "trace_id": trace_id,
                            "span_id": format_span_id(span.context.span_id),
                            "parent_span_id": format_span_id(span.parent.span_id)
                            if span.parent
                            else None,
                            "operation_type": (span.attributes or {}).get(
                                OPERATION_ATTRIBUTE, "runtime"
                            ),
                            "span_kind": span.kind.name.lower(),
                            "events": [
                                {
                                    "name": event.name,
                                    "timestamp": timestamp(event.timestamp),
                                    "attributes": dict(event.attributes or {}),
                                }
                                for event in span.events
                            ],
                        }
                    )
                )
            batch = {"spans": rows}
            if not 1 <= len(rows) <= 500:
                raise ValueError("Trace must contain 1 to 500 spans")
            encoded = json.dumps(batch, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > 1_048_576:
                raise ValueError("Trace batch exceeds ingestion limit")
            response = self._request("POST", "/api/v1/traces", json=trace)
            if response.status_code == 409:
                existing = self._request("GET", f"/api/v1/traces/{trace_id}")
                existing.raise_for_status()
                if not self._same_trace(existing.json(), trace):
                    raise ValueError("Trace conflicts with stored record")
            else:
                response.raise_for_status()
            self._request(
                "POST",
                "/api/v1/spans/batch",
                content=encoded,
                headers={"Content-Type": "application/json"},
            ).raise_for_status()
            self.last_export_succeeded = True
            return True
        except Exception:
            self.last_export_succeeded = False
            logger.warning("Agent Debugger export failed; trace delivery is not guaranteed")
            return False

    def close(self) -> None:
        if self._owns_client:
            try:
                self._client.close()
            except Exception:
                logger.warning("Agent Debugger HTTP client shutdown failed")
