# Examples

`minimal_agent.py` demonstrates Phase 2A explicit tracing, nested local tool calls,
a handled tool exception, fallback, and API export. It makes no LLM calls.

From the repository root, with the Compose services running:

```powershell
uv sync --project sdk --frozen
uv run --project sdk python examples/minimal_agent.py --create-project
```

Reuse the printed project ID with `--project-id <uuid>` on later runs. The Trace and
Span records remain in the development database for review. See `sdk/README.md` for
installation, lifecycle, context propagation, redaction, and delivery limitations.

`instrumented_agent.py` demonstrates Phase 2B explicit Tool/LLM decorators: planning,
a nested catalog read, an optional failed tool, fallback, and final answer. Deterministic
LLM stand-ins need no credentials and report no invented token usage.

```powershell
uv run --project sdk python examples/instrumented_agent.py --create-project
```

```text
Instrumented agent [AGENT, OK; recovery event]
├── Plan action [LLM, OK]
├── Lookup [TOOL, OK]
│   └── Read local catalog [RUNTIME, UNSET]
├── Optional enrichment [TOOL, ERROR; ConnectionError event]
├── Fallback [TOOL, OK]
└── Final answer [LLM, OK]
```

This is explicit decorator instrumentation, not automatic framework interception.
