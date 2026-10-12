"""Explicitly registered trusted callables only; process timeout is not a security sandbox."""

import asyncio
import inspect
import json
import multiprocessing
import pickle
import sys
from collections.abc import Awaitable, Callable
from multiprocessing.connection import Connection
from time import monotonic

from pydantic import ValidationError

from app.schemas.diagnostics import DiagnosticParameters
from app.schemas.evaluation import (
    AgentOutput,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    RunnerConfiguration,
    TraceObservation,
)
from app.services.evaluation import evaluate_case, make_run
from app.services.replay import canonical, digest

AgentFunction = Callable[[EvaluationCase], AgentOutput | Awaitable[AgentOutput]]
TraceResolver = Callable[[str, DiagnosticParameters], TraceObservation | None]


def _worker(
    connection: Connection,
    function: AgentFunction,
    payload: str,
    max_bytes: int,
) -> None:
    """Spawn target must remain top-level for Windows; never accept import paths from datasets."""
    try:
        case = EvaluationCase.model_validate_json(payload)
        try:
            returned = function(case)
            if inspect.isawaitable(returned):
                returned = asyncio.run(returned)
        except Exception:
            # Exception messages/tracebacks may contain credentials or task data.
            connection.send_bytes(b'{"error":"agent_error"}')
            return
        try:
            output = AgentOutput.model_validate(returned)
            encoded = canonical({"output": output.model_dump(mode="json")})
            if len(encoded) > max_bytes:
                raise ValueError("output budget exceeded")
        except (ValidationError, ValueError, TypeError, RecursionError):
            connection.send_bytes(b'{"error":"invalid_output"}')
            return
        connection.send_bytes(encoded)
    finally:
        connection.close()


class TrustedEvaluationRunner:
    """Host code registers importable functions; datasets cannot select source code or commands."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentFunction] = {}

    def register(self, name: str, function: AgentFunction) -> None:
        if (
            not inspect.isfunction(function)
            or "<locals>" in function.__qualname__
            or function.__name__ == "<lambda>"
            or name in self._agents
        ):
            raise ValueError("Registration requires an importable function and unique name")
        try:
            pickle.dumps(function)
        except (pickle.PicklingError, AttributeError, TypeError) as error:
            raise ValueError("Register an importable top-level trusted function") from error
        self._agents[name] = function

    def require_distinct_agents(self, baseline: str, candidate: str) -> None:
        """Preflight both registrations before either implementation has side effects."""
        if baseline not in self._agents or candidate not in self._agents:
            raise ValueError("Both Agents must be explicitly registered")
        if self._agents[baseline] is self._agents[candidate]:
            raise ValueError("Supply distinct baseline and candidate implementations")

    def run(
        self,
        agent: str,
        dataset: EvaluationDataset,
        *,
        run_id: str,
        configuration: RunnerConfiguration | None = None,
        provenance: dict[str, str] | None = None,
        resolve_trace: TraceResolver | None = None,
    ) -> EvaluationRun:
        if agent not in self._agents:
            raise ValueError("Agent has not been explicitly registered")
        config = RunnerConfiguration.model_validate_json(
            (configuration or RunnerConfiguration()).model_dump_json()
        )
        dataset = EvaluationDataset.model_validate_json(dataset.model_dump_json())
        results = []
        context = multiprocessing.get_context("spawn")
        for case in dataset.cases:
            read, write = context.Pipe(duplex=False)
            process = context.Process(
                target=_worker,
                args=(write, self._agents[agent], case.model_dump_json(), config.max_output_bytes),
                daemon=True,
            )
            started = monotonic()
            error, output = None, None
            try:
                process.start()
                write.close()
                remaining = max(0, config.timeout_seconds - (monotonic() - started))
                if read.poll(remaining):
                    message = json.loads(read.recv_bytes(config.max_output_bytes))
                    error = message.get("error")
                    if error is None:
                        output = AgentOutput.model_validate(message["output"])
                else:
                    error = "timeout"
            except (OSError, EOFError, ValueError, KeyError):
                error = "worker_error"
            finally:
                read.close()
                write.close()
                if process.pid is not None:
                    process.join(timeout=0.05)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=1)
                    process.close()
            result = evaluate_case(case, output.value if output else None)
            result.duration_ms = (monotonic() - started) * 1000
            if error:
                result.outcome, result.validation_basis, result.error = (
                    "failed",
                    "execution_error",
                    error,
                )
            elif output is not None:
                result.trace_ids = output.trace_ids
                if len(output.trace_ids) > 1:
                    result.observation_issue = "multiple_traces"
                elif output.trace_ids:
                    result.observation_issue = "unresolved"
                    if resolve_trace is not None:
                        try:
                            observation = resolve_trace(output.trace_ids[0], config.diagnostics)
                            if (
                                observation is not None
                                and observation.analysis.trace_id == output.trace_ids[0]
                                and observation.analysis.parameters == config.diagnostics
                            ):
                                result.observation = observation
                                result.observation_issue = None
                        except Exception:
                            # A read/analysis outage must not replace the captured Agent outcome.
                            pass
            results.append(result)
        function = self._agents[agent]
        try:
            source_digest = digest(inspect.getsource(function))
        except (OSError, TypeError):
            source_digest = "unavailable"
        recorded_provenance = {
            **(provenance or {}),
            "python": sys.version.split()[0],
            "process_start": "spawn",
            "callable": f"{function.__module__}.{function.__qualname__}",
            "callable_source_digest": source_digest,
        }
        return make_run(
            dataset,
            results,
            run_id=run_id,
            agent=agent,
            mode="trusted_execution",
            configuration=config,
            provenance=recorded_provenance,
        )
