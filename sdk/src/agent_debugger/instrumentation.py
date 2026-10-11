"""Explicit function instrumentation; never inspect argument or response contents."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agent_debugger.tracing import AgentDebugger, Operation

P = ParamSpec("P")
R = TypeVar("R")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapturePolicy:
    """Opt in to shape metadata for named arguments and/or the return value.

    Only built-in type names and collection/string lengths are captured. Values,
    dictionary keys, object properties, and repr/str are never inspected.
    """

    arguments: tuple[str, ...] = ()
    result: bool = False


@dataclass(frozen=True)
class LLMUsage:
    """Provider/caller-reported counts; None means unknown, never estimated."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for count in (self.input_tokens, self.output_tokens):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Token counts must be non-negative integers or None")


def openai_usage(response: object) -> LLMUsage:
    """Explicit adapter for dictionary responses with OpenAI-style usage fields.

    Supports Chat Completions and Responses count names, without reading content.
    Missing counts stay unknown. Invalid counts are rejected, not coerced.
    """
    if not isinstance(response, Mapping):
        return LLMUsage()
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        return LLMUsage()
    return LLMUsage(
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
    )


def _shape(span: Span, prefix: str, value: object) -> None:
    # Exact built-in types avoid invoking user-defined __len__, properties or repr.
    allowed = (str, bytes, list, tuple, dict, set, frozenset, int, float, bool, type(None))
    span.set_attribute(
        f"{prefix}.type", type(value).__name__ if type(value) in allowed else "object"
    )
    if type(value) in (str, bytes, list, tuple, dict, set, frozenset):
        span.set_attribute(f"{prefix}.length", len(value))


def _safe_metadata(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        # Adapter errors must not change a successful call or expose its response.
        logger.warning("Agent Debugger instrumentation metadata unavailable")


def _decorate(
    debugger: AgentDebugger,
    operation: Operation,
    name: str | None,
    capture: CapturePolicy,
    attributes: dict[str, Any],
    usage: LLMUsage | Callable[[Any], LLMUsage] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        if inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
            raise TypeError("Tracing decorators do not support generator functions")
        span_name = name or function.__name__
        if not span_name.strip() or len(span_name) > 200 or "\x00" in span_name:
            raise ValueError("Span name must contain 1..200 characters without NUL")
        signature = inspect.signature(function) if capture.arguments else None
        if signature and any(key not in signature.parameters for key in capture.arguments):
            raise ValueError("Capture arguments must name declared function parameters")

        def before(span: Span, args: tuple, kwargs: dict) -> None:
            if signature:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                for key in capture.arguments:
                    _shape(span, f"agent_debugger.arguments.{key}", bound.arguments[key])

        def after(span: Span, result: object) -> None:
            if capture.result:
                _shape(span, "agent_debugger.result", result)
            counts = usage(result) if callable(usage) else usage
            if counts is not None:
                if not isinstance(counts, LLMUsage):
                    raise TypeError("Usage adapters must return LLMUsage")
                for label, count in (
                    ("input", counts.input_tokens),
                    ("output", counts.output_tokens),
                ):
                    if count is not None:
                        span.set_attribute(f"gen_ai.usage.{label}_tokens", count)

        span_attributes = {
            **attributes,
            "gen_ai.tool.name" if operation == "tool" else "gen_ai.operation.name": span_name
            if operation == "tool"
            else "chat",
        }

        @wraps(function)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs):
            if not debugger.is_tracing:
                return await function(*args, **kwargs)
            with debugger.span(
                span_name,
                operation_type=operation,
                attributes=span_attributes,
                kind=SpanKind.CLIENT if operation == "llm" else SpanKind.INTERNAL,
                record_exception_details=False,
            ) as span:
                _safe_metadata(lambda: before(span, args, kwargs))
                result = await function(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                _safe_metadata(lambda: after(span, result))
                return result

        @wraps(function)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
            if not debugger.is_tracing:
                return function(*args, **kwargs)
            with debugger.span(
                span_name,
                operation_type=operation,
                attributes=span_attributes,
                kind=SpanKind.CLIENT if operation == "llm" else SpanKind.INTERNAL,
                record_exception_details=False,
            ) as span:
                _safe_metadata(lambda: before(span, args, kwargs))
                result = function(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                _safe_metadata(lambda: after(span, result))
                return result

        return async_wrapper if inspect.iscoroutinefunction(function) else sync_wrapper

    return decorate


def trace_tool(
    debugger: AgentDebugger, *, name: str | None = None, capture: CapturePolicy | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace a sync/async tool inside debugger.trace(); otherwise call it unchanged."""
    return _decorate(debugger, "tool", name, capture or CapturePolicy(), {})


def trace_llm(
    debugger: AgentDebugger,
    *,
    name: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    usage: LLMUsage | Callable[[Any], LLMUsage] | None = None,
    capture: CapturePolicy | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Explicit LLM call tracing; metadata labels and adapters are caller-supplied."""
    attributes = {}
    for key, value in (("gen_ai.request.model", model), ("gen_ai.provider.name", provider)):
        if value is not None:
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 200
                or "\x00" in value
            ):
                raise ValueError("Model/provider labels must contain 1..200 characters without NUL")
            attributes[key] = value
    return _decorate(debugger, "llm", name, capture or CapturePolicy(), attributes, usage)
