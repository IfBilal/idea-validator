"""One workflow step: budget check, memoisation, timeout, trace span and events (CLAUDE.md 6-9).

Every node that talks to another module goes through ``StepRunner.run`` so that no step can skip
the budget, the trace or the timeout.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from common.budget import BudgetLedger, Spend
from common.errors import (
    BudgetExceeded,
    DomainError,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
)
from common.http import RETRY_STATUS
from common.tracing import Tracer

from ..clients.base import retries_var
from ..repositories.events import RunEmitter
from .cache_keys import canonical_hash, memo_key
from .run_config import RunConfig

TIMEOUT_GRACE_S = 1.0
AGENT = "orchestrator"
PROCESS_ID = uuid.uuid4().hex  # tells a step claimed by this process from one a dead process left


@dataclass(frozen=True)
class StepOutcome:
    value: Any
    cache_hit: bool
    memo_key: str
    memo_update: dict[str, Any]  # merged into ``ValidateState.memo`` by the node


def _retries_after_failure(exc: BaseException, max_retries: int) -> int:
    """ServiceClient retries only timeouts, transport errors, 429/502/503/504 (never 4xx)."""
    if isinstance(exc, UpstreamTimeout | RateLimited):
        return max_retries
    if isinstance(exc, UpstreamError):
        status = exc.details.get("status")
        return max_retries if status is None or status in RETRY_STATUS else 0
    return 0


class StepRunner:
    def __init__(
        self,
        *,
        run_id: str,
        config: RunConfig,
        ledger: BudgetLedger,
        tracer: Tracer,
        emitter: RunEmitter,
        owner: str = PROCESS_ID,
    ) -> None:
        self._owner = owner
        self._run_id = run_id
        self._config = config
        self._ledger = ledger
        self._tracer = tracer
        self._emitter = emitter

    def total_timeout(self, timeout_s: float) -> float:
        """Per-attempt timeout times all attempts, plus a grace for back-off sleeps."""
        return timeout_s * (self._config.max_retries + 1) + TIMEOUT_GRACE_S

    async def run(
        self,
        memo: Mapping[str, Any],
        step: str,
        *,
        agent: str = AGENT,
        tool: str,
        inputs: Any,
        call: Callable[[], Awaitable[Any]],
        timeout_s: float,
        estimate: Spend | None = None,
        memoise: bool = True,
    ) -> StepOutcome:
        estimate = estimate or Spend(steps=1)
        key = memo_key(step, inputs)
        # idea text and profile stay out of traces (docs/harness/13 §8): log a hash only
        traced = {"input_hash": canonical_hash(inputs)[:16]}
        await self._ledger.check()  # an already over-budget run stops even if this step is free

        if memoise and key in memo:
            with self._tracer.span(
                agent, "tool_call", tool=tool, run_id=self._run_id, input=traced
            ) as handle:
                handle.cache_hit = True
                handle.set_output(memo[key])
            await self._emitter.emit(
                "tool_call", agent=agent, tool=tool, cache_hit=True, latency_ms=0
            )
            return StepOutcome(memo[key], True, key, {})

        # A claim left by another process means this step was running when that process died and
        # was already charged: run it again without charging it a second time (recovery, 07 §1).
        rerun = await self._ledger.claim_step(key, self._owner)
        # One atomic check-and-charge: parallel steps can no longer all pass a separate check and
        # overshoot. M3 charges one step per step it runs; M5 charges pages, tokens and cost.
        try:
            if not rerun:
                await self._ledger.reserve(estimate.model_copy(update={"steps": 1}))
        except BudgetExceeded as exc:
            await self._ledger.release_step(key)
            over = exc.details["exceeded"]
            raise BudgetExceeded(
                f"step {step} would exceed the run budget: {', '.join(over)}",
                {"exceeded": over, "step": step, "estimate": estimate.model_dump()},
            ) from exc

        await self._emitter.emit("agent_started", agent=agent, step=step, tool=tool)
        retries_var.set(0)
        error: str | None = None
        started = time.perf_counter()
        try:
            with self._tracer.span(
                agent, "tool_call", tool=tool, run_id=self._run_id, input=traced
            ) as handle:
                try:
                    async with asyncio.timeout(self.total_timeout(timeout_s)):
                        value = await call()
                except TimeoutError as exc:
                    handle.retries = self._config.max_retries
                    raise UpstreamTimeout(f"step {step} timed out after {timeout_s}s") from exc
                except DomainError as exc:
                    handle.retries = _retries_after_failure(exc, self._config.max_retries)
                    raise
                handle.retries = retries_var.get()
                handle.set_output(value)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            await self._ledger.release_step(
                key
            )  # a cancelled step keeps its claim, a failed one not
            raise
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            spent = await self._ledger.spent()  # the step itself was charged by reserve() above
            limits = await self._ledger.limits()
            await self._emitter.emit(
                "tool_call",
                agent=agent,
                tool=tool,
                cache_hit=False,
                latency_ms=latency_ms,
                **({"error": error} if error else {}),
            )
            await self._emitter.emit(
                "spend_update", spent=spent.model_dump(), budget=limits.model_dump()
            )
            await self._emitter.emit("agent_finished", agent=agent, step=step)
        await self._ledger.release_step(key)
        return StepOutcome(value, False, key, {key: value} if memoise else {})

    def decision(self, name: str, inputs: Any, output: Any) -> None:
        """A routing or policy decision as a ``kind=decision`` trace record (docs/harness/07 §8)."""
        with self._tracer.span(
            AGENT, "decision", tool="transition", run_id=self._run_id, input=inputs
        ) as handle:
            handle.set_output({"decision": name, **(output if isinstance(output, dict) else {})})
