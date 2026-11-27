"""Ledger support for run recovery: step claims (no double billing) and restoring a lost hash.

Runs against the in-memory emulation and a real Redis so the two cannot drift apart.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from common.budget import BudgetLedger, BudgetLimits, Spend
from common.testing import EmulatedRedis

LIMITS = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=10, max_cost_usd=0.05)


@pytest.fixture(params=["emulated", "real"])
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
    assert await ledger(redis).claim_step("k1", "proc_a") is False


async def test_a_claim_left_by_another_process_marks_a_rerun(redis: Any) -> None:
    await ledger(redis).claim_step("k1", "proc_a")  # proc_a dies mid-step: no release
    assert await ledger(redis).claim_step("k1", "proc_b") is True


async def test_a_claim_by_the_same_process_is_not_a_rerun(redis: Any) -> None:
    """Two identical steps in flight at once in one process are both real work."""
    led = ledger(redis)
    await led.claim_step("k1", "proc_a")
    assert await led.claim_step("k1", "proc_a") is False


async def test_a_released_step_can_be_billed_again(redis: Any) -> None:
    led = ledger(redis)
    await led.claim_step("k1", "proc_a")
    await led.release_step("k1")
    assert await ledger(redis).claim_step("k1", "proc_b") is False


async def test_releasing_an_unclaimed_step_is_harmless(redis: Any) -> None:
    await ledger(redis).release_step("never_claimed")


async def test_claims_are_per_step_and_per_run(redis: Any) -> None:
    await ledger(redis, "run_1").claim_step("k1", "proc_a")
    assert await ledger(redis, "run_1").claim_step("k2", "proc_b") is False
    assert await ledger(redis, "run_2").claim_step("k1", "proc_b") is False


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
