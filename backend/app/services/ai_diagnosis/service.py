import threading
import time
from collections import OrderedDict

from app.core.ai_config import AISettings
from app.schemas.ai_diagnosis import AIDiagnosisResponse, ModelExecution
from app.schemas.diagnostics import DiagnosticParameters
from app.schemas.traces import SpanRead, TraceRead
from app.services.ai_diagnosis.context import build_context
from app.services.ai_diagnosis.provider import AnalysisProvider, CompatibleProvider, ProviderFailure
from app.services.ai_diagnosis.validation import validate_output
from app.services.diagnostics import analyze_trace


class AnalysisGuard:
    """Bounded per-process anti-repeat guard, not authentication or distributed rate limiting."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.recent: OrderedDict[str, float] = OrderedDict()
        self.active: set[str] = set()

    def reserve(self, key: str, settings: AISettings) -> str | None:
        now = time.monotonic()
        with self.lock:
            if (
                key in self.active
                or now - self.recent.get(key, float("-inf")) < settings.cooldown_seconds
            ):
                return "analysis_cooldown"
            if len(self.active) >= settings.max_concurrent:
                return "analysis_busy"
            self.active.add(key)
            self.recent[key] = now
            self.recent.move_to_end(key)
            while len(self.recent) > 1024:
                self.recent.popitem(last=False)
        return None

    def release(self, key: str) -> None:
        with self.lock:
            self.active.discard(key)


class AIDiagnosisService:
    def __init__(
        self,
        settings: AISettings,
        parameters: DiagnosticParameters,
        guard: AnalysisGuard,
        provider: AnalysisProvider | None = None,
    ) -> None:
        self.settings, self.parameters, self.guard = settings, parameters, guard
        self.provider = provider or CompatibleProvider(settings)

    def analyze(
        self, snapshot: tuple[TraceRead, list[SpanRead]], consent: bool
    ) -> AIDiagnosisResponse:
        trace, spans = snapshot
        deterministic = analyze_trace(trace, spans, self.parameters)
        context = build_context(trace, spans, deterministic, self.settings)
        result = AIDiagnosisResponse(
            trace_id=trace.trace_id,
            deterministic=deterministic,
            context=context.report,
            evidence=context.facts,
            execution=ModelExecution(status="disabled", message="External analysis is disabled."),
        )

        def stop(status: str, message: str, code: str | None = None) -> AIDiagnosisResponse:
            result.execution = ModelExecution(status=status, message=message, error_code=code)
            return result

        if not self.settings.enabled:
            return result
        if not consent:
            return stop(
                "consent_required", "Explicit consent to send the allowlisted context is required."
            )
        if not deterministic.findings and deterministic.operation_failure_count == 0:
            return stop("not_needed", "No recorded diagnostic finding requires external analysis.")
        if not context.fits or (spans and not context.report.included_span_ids):
            return stop("failed", "Context cannot fit the configured input budget.", "input_limit")
        key = f"{trace.project_id}:{trace.trace_id}"
        blocked = self.guard.reserve(key, self.settings)
        if blocked:
            return stop(
                "rate_limited", "Analysis is busy or within its cooldown; try later.", blocked
            )
        start = time.monotonic()
        attempts = 0
        try:
            response = self.provider.analyze(context.payload)
            attempts = response.attempts
            if len(response.content.encode()) > self.settings.max_response_bytes:
                raise ProviderFailure("response_too_large", attempts)
            output = validate_output(response.content, context, {span.span_id for span in spans})
            result.hypotheses = output.hypotheses
            result.execution = ModelExecution(
                status="completed",
                message="Validated hypotheses; underlying causes remain unverified.",
                model=self.settings.model,
                attempts=attempts,
                elapsed_ms=(time.monotonic() - start) * 1000,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
        except ProviderFailure as error:
            result.execution = ModelExecution(
                status="failed",
                message="AI analysis unavailable or rejected; "
                "deterministic findings remain available.",
                model=self.settings.model,
                attempts=max(attempts, error.attempts),
                error_code=error.code,
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        except Exception:
            # Extension providers must never expose raw errors, credentials or model bodies.
            result.execution = ModelExecution(
                status="failed",
                message="AI analysis unavailable; deterministic findings remain available.",
                model=self.settings.model,
                attempts=attempts,
                error_code="provider_unavailable",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        finally:
            self.guard.release(key)
        return result
