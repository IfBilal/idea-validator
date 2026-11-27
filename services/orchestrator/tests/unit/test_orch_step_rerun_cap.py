"""Free re-runs of a step cut off by a crash are capped, and never free if it was not charged."""

import asyncio
from typing import Any

import pytest

from common.budget import BudgetLedger, BudgetLimits
from common.errors import BudgetExceeded
from common.tracing import Tracer
from svc_orchestrator.repositories.events import InMemoryPublisher, RunEmitter
from svc_orchestrator.services.run_config import run_config_from
from svc_orchestrator.services.steps import MAX_STEP_RERUNS, StepRunner
from svc_orchestrator.testing import FakeRedis

LIMITS = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=10, max_cost_usd=1.0)


def runner(redis: FakeRedis, owner: str) -> StepRunner:
    config = run_config_from(
        {
            "validate": {
                "max_retries": 0,
                "max_tokens": 1000,
                "max_pages": 10,
                "max_steps": 10,
                "max_cost_usd": 1.0,
            }
        },
        "validate",
    )
    return StepRunner(
        run_id="run_1",
        config=config,
        ledger=BudgetLedger(redis, "run_1", LIMITS),
        tracer=Tracer("orchestrator", sink=lambda record: None),
        emitter=RunEmitter("run_1", InMemoryPublisher()),
        owner=owner,
    )


async def step(r: StepRunner, call: Any) -> Any:
    return await r.run({}, "kb", tool="kb.search", inputs=1, call=call, timeout_s=1)


async def ok() -> dict[str, bool]:
    return {"ok": True}


async def crash() -> None:
    raise asyncio.CancelledError


async def spent_steps(redis: FakeRedis) -> int:
    return (await BudgetLedger(redis, "run_1", LIMITS).spent()).steps


async def test_a_poison_step_cannot_be_re_run_for_free_forever() -> None:
    redis = FakeRedis()
    for n in range(MAX_STEP_RERUNS + 1):  # the first run plus every allowed re-run dies on it
        with pytest.raises(asyncio.CancelledError):
            await step(runner(redis, f"dead{n}"), crash)
    with pytest.raises(BudgetExceeded) as caught:
        await step(runner(redis, "next"), ok)
    assert caught.value.details["exceeded"] == ["reruns"]
    assert await spent_steps(redis) == 1  # and it never got more than the first charge


async def test_the_re_runs_up_to_the_cap_are_allowed() -> None:
    redis = FakeRedis()
    for n in range(MAX_STEP_RERUNS):
        with pytest.raises(asyncio.CancelledError):
            await step(runner(redis, f"dead{n}"), crash)
    assert (await step(runner(redis, "last"), ok)).value == {"ok": True}


async def test_a_crash_between_the_claim_and_the_charge_does_not_make_the_rerun_free() -> None:
    redis = FakeRedis()
    first = runner(redis, "dead")

    async def die(*args: Any, **kwargs: Any) -> None:
        raise asyncio.CancelledError

    first._ledger.reserve = die  # type: ignore[method-assign, assignment]
    with pytest.raises(asyncio.CancelledError):
        await step(first, ok)
    assert await spent_steps(redis) == 0
    await step(runner(redis, "new"), ok)
    assert await spent_steps(redis) == 1
