"""Phase 4A on-demand deterministic diagnostics; no providers or persistence."""

from app.services.diagnostics.engine import analyze_trace

__all__ = ["analyze_trace"]
