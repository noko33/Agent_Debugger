"""Vetted conditional changes: never substitute unseen prompt/tool content or thresholds."""

TEMPLATES = {
    "repeated": (
        "TOOL_POLICY",
        "Review repeated tool failures",
        "Repeated failed calls share a recorded label; identical inputs or retries are not proven.",
        "Review failure classification and choose a bounded fallback or stop policy for "
        "non-retryable failures. Check tool identity before changing its policy.",
        "In a controlled comparison, fewer repeated failed calls without losing valid results.",
        "Stopping early may discard a transiently recoverable result; labels may collide.",
        "Tool identity, retryability and fallback safety must be reviewed locally.",
    ),
    "retry": (
        "RUNTIME",
        "Review explicit retry bounds",
        "Explicit consecutive failed attempts support a retry warning, not an infinite-loop claim.",
        "Review existing attempt and elapsed-time limits; test bounded retries with backoff "
        "only for retryable failures. Stop or use a reviewed fallback when the limit is reached.",
        "Recorded attempts terminate within the chosen budget in a controlled failure case.",
        "Backoff increases latency; tighter bounds may reduce recovery opportunities.",
        "Establish idempotency, retryable error classes and task latency requirements first.",
    ),
    "slow": (
        "RUNTIME",
        "Profile the recorded slow operation",
        "A recorded interval exceeds the configured rule threshold; the bottleneck is unknown.",
        "Profile child operations and waiting time before selecting a timeout or execution "
        "budget. Test cancellation and cleanup before introducing a reviewed limit.",
        "Comparable traces identify where time is spent and whether a chosen limit is respected.",
        "Inclusive parent timing overlaps children; an arbitrary timeout can interrupt valid work.",
        "Define comparable workloads and acceptable latency; do not sum overlapping spans.",
    ),
    "missing": (
        "INSTRUMENTATION",
        "Complete the missing trace evidence",
        "The recorded trace is incomplete or structurally inconsistent; a repair cause is unknown.",
        "Record missing span ends, parent links, final statuses and explicit recovery evidence "
        "as applicable. Verify exporter completion without collecting secrets or raw payloads.",
        "A controlled run has valid relationships, completed intervals and fewer "
        "data-quality findings.",
        "Additional instrumentation costs time and storage; indiscriminate payload "
        "capture leaks data.",
        "Identify the missing signals from the finding before adding only the required metadata.",
    ),
    "tool_unrecovered": (
        "TOOL_POLICY",
        "Review unrecovered tool failure handling",
        "A tool failure and an unrecovered execution are recorded; causal "
        "responsibility is unknown.",
        "Review error handling for the referenced tool. Test an explicit safe fallback or clear "
        "terminal failure policy; retain existing safeguards and avoid blind retries.",
        "Controlled tool failures produce the intended terminal status or an "
        "explicitly recorded recovery.",
        "Fallbacks may return incomplete results or repeat side effects; recovery "
        "is not semantic success.",
        "Verify failure semantics, side effects and fallback suitability before changing behavior.",
    ),
    "tool_recovered": (
        "TOOL_POLICY",
        "Validate the existing recovery policy",
        "A tool operation failed while execution recovery was explicitly recorded.",
        "Keep the existing recovery behavior until reviewed. Exercise its fallback path under "
        "controlled failure and document terminal conditions rather than treating "
        "recovery as a defect.",
        "The recovery path consistently records its outcome and meets locally "
        "defined result checks.",
        "A recorded recovery can mask an incomplete answer; replacing a working "
        "fallback may regress it.",
        "Review fallback acceptance criteria and the explicit recovery evidence.",
    ),
    "unknown": (
        "INSTRUMENTATION",
        "Record failure and outcome context",
        "An operation failed, but the available evidence does not establish a "
        "suitable behavioral fix.",
        "Record a safe error class, terminal root status and explicit handling/recovery signals. "
        "For LLM calls, record provider error category and validation outcome, not prompt content.",
        "A controlled run distinguishes operation failure, provider errors and execution outcome.",
        "Recording detailed errors or content may expose secrets; use structured "
        "allowlisted signals.",
        "Determine which required signals are absent without inferring prompt "
        "defects or service outages.",
    ),
    "prompt": (
        "PROMPT",
        "Review instructions against a recorded validation failure",
        "Caller-supplied instruction-compliance validation is explicitly recorded as failed. "
        "This is a reported check, not proof that the prompt caused the failure.",
        "Locally review the failing validator and instruction contract. If the validator is valid, "
        "test clearer acceptance constraints and an explicit uncertainty/fallback instruction.",
        "A controlled held-out comparison satisfies the reviewed validator without "
        "regressing other checks.",
        "A faulty validator or over-specific instructions can overfit examples and "
        "reduce useful answers.",
        "Verify validator correctness and inspect redacted instructions locally "
        "before proposing edits.",
    ),
}
