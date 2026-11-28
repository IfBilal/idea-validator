"""Trace spans: every agent step, tool call, model call and decision becomes a TraceRecord.

Records go to stdout as a JSON line and to an optional sink (``common.trace_sink.AgentOpsSink``).
Tracing must never break a run, so sink failures are logged and swallowed.
"""

import json
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from .context import parent_step_var, run_id_var
from .errors import DomainError
from .logging import get_logger
from .redaction import redact
from .schemas.trace import TraceKind, TraceRecord

MAX_PAYLOAD_CHARS = 4096
DEFAULT_MAX_RUNS = 10_000  # step counters kept per process; the least recently used run is dropped

Sink = Callable[[TraceRecord], None]


def bound_payload(value: Any, limit: int = MAX_PAYLOAD_CHARS) -> Any:
    """Redact secrets and cap payloads at 4 KB (docs/harness/12 §1). M13 applies it again."""
    safe = redact(value)
    text = json.dumps(safe, default=str, sort_keys=True)
    if len(text) <= limit:
        return json.loads(text)
    return {"_truncated": True, "preview": text[:limit]}


_bounded = bound_payload


MAX_ERROR_CHARS = 200


_SAFE_MESSAGES: set[str] = set()


def register_trace_safe_message(message: str) -> None:
    """Declare a fixed reason (no user data in it) that may appear in a trace as is."""
    _SAFE_MESSAGES.add(message)


def error_text(exc: BaseException) -> str:
    """What a trace may say about a failure: our own messages, capped and redacted; for anything
    else only the type, because str(exc) can embed user text (pydantic prints the input)."""
    name = type(exc).__name__
    if isinstance(exc, DomainError):
        return f"{name}: {redact(exc.message[:MAX_ERROR_CHARS])}"
    if isinstance(exc, ValidationError):
        return f"{name}: {exc.error_count()} validation errors"
    if type(exc).__module__ == "builtins" and str(exc) in _SAFE_MESSAGES:
        return f"{name}: {exc}"  # one of our own fixed reasons, never user text
    return name


class SpanHandle:
    """Mutable view of a span: fill in what you learn while the step runs."""

    def __init__(self, step_id: int) -> None:
        self.step_id = step_id
        self.output: Any = None
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.retries = 0
        self.cache_hit = False
        self.fallback: bool | None = None
        self.model: str | None = None
        self.error: str | None = None

    def set_output(self, output: Any) -> None:
        self.output = output

    def set_usage(self, *, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> None:
        self.tokens_in, self.tokens_out, self.cost_usd = tokens_in, tokens_out, cost_usd


class Tracer:
    def __init__(
        self, service: str, sink: Sink | None = None, max_runs: int = DEFAULT_MAX_RUNS
    ) -> None:
        self._service = service
        self._sink = sink
        self.max_runs = max_runs
        # bounded: a long-lived service sees an unbounded number of run ids
        self._counters: OrderedDict[str, int] = OrderedDict()
        self._log = get_logger("trace")

    @property
    def tracked_runs(self) -> int:
        return len(self._counters)

    def _next_step(self, run_id: str) -> int:
        step = self._counters.pop(run_id, 0) + 1
        self._counters[run_id] = step  # re-inserted last = most recently used
        while len(self._counters) > self.max_runs:
            self._counters.popitem(last=False)
        return step

    @contextmanager
    def span(
        self,
        agent: str,
        kind: TraceKind,
        *,
        tool: str | None = None,
        run_id: str | None = None,
        model: str | None = None,
        input: Any = None,
        prompt_version: str | None = None,
        rubric_version: str | None = None,
        input_limit: int = MAX_PAYLOAD_CHARS,
    ) -> Iterator[SpanHandle]:
        rid = run_id or run_id_var.get()
        if rid is None:
            raise ValueError("a run_id is required (argument or run_id_var)")
        step_id = self._next_step(rid)
        parent = parent_step_var.get()
        handle = SpanHandle(step_id)
        handle.model = model
        token = parent_step_var.set(step_id)
        started = time.perf_counter()
        try:
            yield handle
        except Exception as exc:
            handle.error = error_text(exc)
            raise
        finally:
            parent_step_var.reset(token)
            latency_ms = int((time.perf_counter() - started) * 1000)
            record = TraceRecord(
                run_id=rid,
                step_id=step_id,
                parent_step=parent,
                agent=agent,
                kind=kind,
                tool=tool,
                input=bound_payload(input, input_limit),
                output=_bounded(handle.output),
                tokens_in=handle.tokens_in,
                tokens_out=handle.tokens_out,
                cost_usd=handle.cost_usd,
                latency_ms=latency_ms,
                retries=handle.retries,
                model=handle.model,
                cache_hit=handle.cache_hit,
                error=handle.error,
                ts=datetime.now(UTC),
                service=self._service,
                prompt_version=prompt_version,
                rubric_version=rubric_version,
                fallback=handle.fallback,
            )
            self._emit(record)

    def _emit(self, record: TraceRecord) -> None:
        self._log.info("trace_record", **record.model_dump(mode="json"))
        if self._sink is not None:
            try:
                self._sink(record)
            except Exception as exc:  # tracing must never break a run
                self._log.warning("trace_sink_failed", error=str(exc))
