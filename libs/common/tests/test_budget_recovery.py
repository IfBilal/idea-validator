"""Ledger support for run recovery: step claims (no double billing) and restoring a lost hash.

Runs against the in-memory emulation and a real Redis so the two cannot drift apart.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from common.budget import BudgetLedger, BudgetLimits, Spend, StepClaim
from common.testing import EmulatedRedis

LIMITS = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=10, max_cost_usd=0.05)


@pytest.fixture(params=["emulated", pytest.param("real", marks=pytest.mark.integration)])
async def redis(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "emulated":
        yield EmulatedRedis()
        return
    client = Redis.from_url(request.getfixturevalue("redis_url"))
    await client.flushdb()
    yield client
    await client.aclose()


def ledger(redis: Any, run_id: str = "run_1") -> BudgetLedger:
    return BudgetLedger(redis, run_id, LIMITS)


async def test_the_first_claim_of_a_step_is_not_a_rerun(redis: Any) -> None:
    assert await ledger(redis).claim_step("k1", "proc_a") == StepClaim(free=False, reruns=0)


async def test_a_rerun_after_a_paid_step_is_free_and_counted(redis: Any) -> None:
    led = ledger(redis)
    await led.claim_step("k1", "proc_a")
    await led.mark_paid("k1")  # proc_a dies mid-step after being charged
    assert await ledger(redis).claim_step("k1", "proc_b") == StepClaim(free=True, reruns=1)
    assert await ledger(redis).claim_step("k1", "proc_c") == StepClaim(free=True, reruns=2)


async def test_a_rerun_of_a_step_that_was_never_charged_is_charged(redis: Any) -> None:
    """A crash between the claim and the charge must not leave a free step behind."""
    await ledger(redis).claim_step("k1", "proc_a")  # dies before reserve + mark_paid
    claim = await ledger(redis).claim_step("k1", "proc_b")
    assert claim == StepClaim(free=False, reruns=1)


async def test_a_claim_by_the_same_process_is_not_a_rerun(redis: Any) -> None:
    """Two identical steps in flight at once in one process are both real work."""
    led = ledger(redis)
    await led.claim_step("k1", "proc_a")
    await led.mark_paid("k1")
    assert await led.claim_step("k1", "proc_a") == StepClaim(free=False, reruns=0)


async def test_a_released_step_can_be_billed_again(redis: Any) -> None:
    led = ledger(redis)
    await led.claim_step("k1", "proc_a")
    await led.mark_paid("k1")
    await led.release_step("k1")
    assert await ledger(redis).claim_step("k1", "proc_b") == StepClaim(free=False, reruns=0)


async def test_releasing_an_unclaimed_step_is_harmless(redis: Any) -> None:
    await ledger(redis).release_step("never_claimed")


async def test_claims_are_per_step_and_per_run(redis: Any) -> None:
    await ledger(redis, "run_1").claim_step("k1", "proc_a")
    assert (await ledger(redis, "run_1").claim_step("k2", "proc_b")).reruns == 0
    assert (await ledger(redis, "run_2").claim_step("k1", "proc_b")).reruns == 0


async def test_claims_expire_with_the_ledger_ttl(redis: Any) -> None:
    led = BudgetLedger(redis, "run_1", LIMITS, ttl_s=30)
    await led.claim_step("k1", "proc_a")
    if isinstance(redis, EmulatedRedis):
        assert redis.ttls["budget:run_1:claim:k1"] == 30
    else:
        assert 0 < await redis.ttl("budget:run_1:claim:k1") <= 30


async def test_an_empty_ledger_is_restored_from_the_stored_spend(redis: Any) -> None:
    led = ledger(redis)
    stored = Spend(tokens=120, pages=3, steps=7, cost_usd=0.02)
    assert await led.restore_if_empty(stored) is True
    assert await led.spent() == stored


async def test_a_ledger_that_has_spend_is_never_overwritten(redis: Any) -> None:
    led = ledger(redis)
    await led.record(Spend(steps=2))
    assert await led.restore_if_empty(Spend(steps=9, tokens=5)) is False
    assert (await led.spent()).steps == 2


async def test_restoring_nothing_is_a_no_op(redis: Any) -> None:
    led = ledger(redis)
    assert await led.restore_if_empty(Spend()) is False
    assert await led.spent() == Spend()


async def test_a_restore_cannot_push_a_run_over_budget_silently(redis: Any) -> None:
    """Restored spend over the limit is kept as is: the next step's check stops the run."""
    led = ledger(redis)
    await led.restore_if_empty(Spend(steps=11))
    assert await led.exceeded_fields() == ["steps"]


async def test_the_cost_raise_of_an_approval_is_restored_with_the_spend(redis: Any) -> None:
    led = ledger(redis)
    assert await led.restore_if_empty(Spend(cost_usd=0.04), cost_bonus_usd=0.02) is True
    assert (await led.limits()).max_cost_usd == pytest.approx(0.07)
    assert (await led.spent()).cost_usd == pytest.approx(0.04)


async def test_a_bonus_alone_is_restored(redis: Any) -> None:
    led = ledger(redis)
    assert await led.restore_if_empty(Spend(), cost_bonus_usd=0.02) is True
    assert (await led.limits()).max_cost_usd == pytest.approx(0.07)


async def test_a_negative_bonus_is_refused(redis: Any) -> None:
    with pytest.raises(ValueError):
        await ledger(redis).restore_if_empty(Spend(), cost_bonus_usd=-1)


async def test_two_restores_racing_apply_once(redis: Any) -> None:
    import asyncio

    led = ledger(redis)
    results = await asyncio.gather(*(led.restore_if_empty(Spend(steps=7)) for _ in range(10)))
    assert sum(results) == 1
    assert (await led.spent()).steps == 7  # not 70: the check and the write are one step


async def test_a_ledger_with_only_a_cost_raise_counts_as_existing(redis: Any) -> None:
    led = ledger(redis)
    await led.raise_cost_limit(0.01)
    assert await led.restore_if_empty(Spend(steps=3)) is False
