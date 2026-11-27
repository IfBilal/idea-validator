"""BudgetLedger.reserve: the check and the charge are one atomic Redis step (CLAUDE.md rule 8).

The earlier read-check-then-record sequence let parallel steps all pass the check and overshoot
(`max_steps=4` ended at 7 with four parallel searches). The same scenarios run against an in-memory
emulation (used by unit tests of other services) and against a real Redis, so the Lua script and its
emulation cannot drift apart.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from common.budget import BudgetLedger, BudgetLimits, Spend
from common.errors import BudgetExceeded
from common.testing import EmulatedRedis

LIMITS = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=10, max_cost_usd=0.05)


@pytest.fixture(params=["emulated", "real"])
async def redis(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "emulated":
        yield EmulatedRedis()
        return
    request.getfixturevalue("redis_url")  # skips cleanly when Docker is unavailable
    client = Redis.from_url(request.getfixturevalue("redis_url"))
    await client.flushdb()
    yield client
    await client.aclose()


def ledger(redis: Any, run_id: str = "run_1", limits: BudgetLimits = LIMITS) -> BudgetLedger:
    return BudgetLedger(redis, run_id, limits)


async def test_a_reservation_within_the_limits_charges_only_the_steps(redis: Any) -> None:
    led = ledger(redis)
    await led.reserve(Spend(steps=1, pages=5, cost_usd=0.01, tokens=100))
    spent = await led.spent()
    # pages, tokens and cost are charged by M5 itself: M3 must not count them a second time
    assert (spent.steps, spent.pages, spent.tokens, spent.cost_usd) == (1, 0, 0, 0.0)


@pytest.mark.parametrize(
    ("estimate", "over"),
    [
        (Spend(steps=11), ["steps"]),
        (Spend(steps=1, pages=11), ["pages"]),
        (Spend(steps=1, tokens=1001), ["tokens"]),
        (Spend(steps=1, cost_usd=0.051), ["cost_usd"]),
        (
            Spend(steps=11, pages=11, tokens=1001, cost_usd=1),
            ["tokens", "pages", "steps", "cost_usd"],
        ),
    ],
)
async def test_a_reservation_over_any_limit_is_refused_and_changes_nothing(
    redis: Any, estimate: Spend, over: list[str]
) -> None:
    led = ledger(redis)
    await led.reserve(Spend(steps=2))
    with pytest.raises(BudgetExceeded) as refused:
        await led.reserve(estimate)
    assert refused.value.details["exceeded"] == over
    assert (await led.spent()).steps == 2, "a refused reservation must not charge anything"


async def test_the_limit_itself_is_allowed_and_one_more_is_not(redis: Any) -> None:
    led = ledger(redis)
    for _ in range(10):
        await led.reserve(Spend(steps=1))
    with pytest.raises(BudgetExceeded):
        await led.reserve(Spend(steps=1))
    assert (await led.spent()).steps == 10


async def test_float_noise_in_the_cost_does_not_refuse_an_exactly_fitting_reservation(
    redis: Any,
) -> None:
    led = ledger(redis)
    await redis.hincrbyfloat("budget:run_1", "cost_usd", 0.01)
    for _ in range(4):
        await redis.hincrbyfloat("budget:run_1", "cost_usd", 0.01)  # 0.01 * 5 != 0.05 in binary
    await led.reserve(Spend(steps=1, cost_usd=0.0))  # spent is 0.05 within tolerance: fits
    with pytest.raises(BudgetExceeded):
        await led.reserve(Spend(steps=1, cost_usd=0.001))


async def test_an_approved_cost_raise_is_honoured_by_the_reservation(redis: Any) -> None:
    led = ledger(redis)
    with pytest.raises(BudgetExceeded):
        await led.reserve(Spend(steps=1, cost_usd=0.08))
    await led.raise_cost_limit(0.05)
    await led.reserve(Spend(steps=1, cost_usd=0.08))
    assert (await led.spent()).steps == 1


async def test_budgets_are_per_run_and_expire(redis: Any) -> None:
    a, b = ledger(redis, "run_a"), ledger(redis, "run_b")
    for _ in range(10):
        await a.reserve(Spend(steps=1))
    await b.reserve(Spend(steps=1))  # run_a being full does not affect run_b
    assert (await b.spent()).steps == 1
    assert await a.ttl_s() > 0


@pytest.mark.integration
async def test_forty_parallel_reservations_admit_exactly_the_limit(redis_url: str) -> None:
    """The bug this fixes: parallel steps all passed a separate check and overshot the step cap."""
    client = Redis.from_url(redis_url)
    await client.flushdb()
    led = ledger(client, "run_race")

    async def attempt() -> bool:
        try:
            await led.reserve(Spend(steps=1))
            return True
        except BudgetExceeded:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(40)))
    assert sum(results) == 10, f"{sum(results)} reservations were admitted for a limit of 10"
    assert (await led.spent()).steps == 10
    await client.aclose()
