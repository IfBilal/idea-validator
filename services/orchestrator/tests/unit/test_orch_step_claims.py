"""A step that was in flight when its process died is re-run once without being billed twice."""

import asyncio
from typing import Any

import pytest

from common.budget import BudgetLedger, BudgetLimits, Spend
from common.errors import BudgetExceeded, UpstreamError
from common.tracing import Tracer
from svc_orchestrator.repositories.events import InMemoryPublisher, RunEmitter
from svc_orchestrator.services.run_config import run_config_from
from svc_orchestrator.services.steps import StepRunner
from svc_orchestrator.testing import FakeRedis


def runner(redis: FakeRedis, owner: str, *, max_steps: int = 10) -> StepRunner:
    limits = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=max_steps, max_cost_usd=1.0)
    config = run_config_from(
        {
            "validate": {
                "max_retries": 0,
                "max_tokens": 1000,
                "max_pages": 10,
                "max_steps": max_steps,
                "max_cost_usd": 1.0,
            }
        },
        "validate",
    )
    return StepRunner(
        run_id="run_1",
        config=config,
        ledger=BudgetLedger(redis, "run_1", limits),
        tracer=Tracer("orchestrator", sink=lambda record: None),
        emitter=RunEmitter("run_1", InMemoryPublisher()),
        owner=owner,
    )


async def step(r: StepRunner, call: Any, inputs: Any = 1, **kw: Any) -> Any:
    return await r.run({}, "kb", tool="kb.search", inputs=inputs, call=call, timeout_s=1, **kw)


async def ok() -> dict[str, bool]:
    return {"ok": True}


async def steps_spent(redis: FakeRedis) -> int:
    return int((await BudgetLedger(redis, "run_1", LIMITS).spent()).steps)


LIMITS = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=10, max_cost_usd=1.0)


async def crash_midway() -> None:
    raise asyncio.CancelledError  # the process is gone: nothing runs after this


async def test_a_step_that_completes_leaves_no_claim_behind() -> None:
    redis = FakeRedis()
    await step(runner(redis, "p1"), ok)
    assert not [k for k in redis.values if ":claim:" in k]
    assert await steps_spent(redis) == 1


async def test_a_step_that_fails_leaves_no_claim_so_a_retry_is_billed() -> None:
    redis = FakeRedis()

    async def fail() -> None:
        raise UpstreamError("down")

    with pytest.raises(UpstreamError):
        await step(runner(redis, "p1"), fail)
    await step(runner(redis, "p1"), ok)
    assert await steps_spent(redis) == 2


async def test_a_step_cut_off_by_a_crash_is_not_billed_again_after_recovery() -> None:
    redis = FakeRedis()
    with pytest.raises(asyncio.CancelledError):
        await step(runner(redis, "dead"), crash_midway)
    assert await steps_spent(redis) == 1  # reserved before the crash
    outcome = await step(runner(redis, "new"), ok)  # recovery re-runs it
    assert outcome.value == {"ok": True}
    assert await steps_spent(redis) == 1  # not charged a second time


async def test_only_the_first_rerun_is_free() -> None:
    redis = FakeRedis()
    with pytest.raises(asyncio.CancelledError):
        await step(runner(redis, "dead"), crash_midway)
    await step(runner(redis, "new"), ok)
    await step(runner(redis, "new"), ok)  # a genuinely new run of the same step
    assert await steps_spent(redis) == 2


async def test_two_identical_steps_in_flight_in_one_process_are_both_billed() -> None:
    redis = FakeRedis()
    r = runner(redis, "p1")
    await asyncio.gather(step(r, ok, memoise=False), step(r, ok, memoise=False))
    assert await steps_spent(redis) == 2


async def test_a_different_step_after_a_crash_is_billed_normally() -> None:
    redis = FakeRedis()
    with pytest.raises(asyncio.CancelledError):
        await step(runner(redis, "dead"), crash_midway, inputs=1)
    await step(runner(redis, "new"), ok, inputs=2)
    assert await steps_spent(redis) == 2


async def test_a_free_rerun_still_refuses_a_run_that_is_already_over_budget() -> None:
    redis = FakeRedis()
    with pytest.raises(asyncio.CancelledError):
        await step(runner(redis, "dead", max_steps=1), crash_midway)
    await BudgetLedger(redis, "run_1", LIMITS).record(Spend(steps=20))
    with pytest.raises(BudgetExceeded):
        await step(runner(redis, "new", max_steps=1), ok)


async def test_a_refused_step_does_not_leave_a_claim_that_would_waive_a_later_charge() -> None:
    redis = FakeRedis()
    with pytest.raises(BudgetExceeded):
        await step(runner(redis, "p1", max_steps=0), ok)
    assert not [k for k in redis.values if ":claim:" in k]
