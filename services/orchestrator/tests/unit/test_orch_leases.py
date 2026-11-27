"""Run leases: exactly one replica drives a run (single-flight across restarts and replicas)."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from redis.asyncio import Redis

from svc_orchestrator.repositories.leases import InMemoryLeases, RedisLeases, RunLeases

Make = Callable[[str, int], RunLeases]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(
    params=["memory", pytest.param("real", marks=pytest.mark.integration)],
)
async def make(request: pytest.FixtureRequest) -> AsyncIterator[Make]:
    if request.param == "memory":
        table: dict[str, tuple[str, float]] = {}
        clock = Clock()
        request.node.clock = clock
        yield lambda owner, ttl: InMemoryLeases(table, owner, ttl_s=ttl, clock=clock)
        return
    client = Redis.from_url(request.getfixturevalue("redis_url"))
    await client.flushdb()
    yield lambda owner, ttl: RedisLeases(client, owner, ttl_s=ttl)
    await client.aclose()


async def test_the_first_replica_gets_the_lease_and_the_second_does_not(make: Make) -> None:
    a, b = make("a", 60), make("b", 60)
    assert await a.acquire("run_1") is True
    assert await b.acquire("run_1") is False


async def test_acquiring_twice_by_the_same_owner_is_allowed(make: Make) -> None:
    a = make("a", 60)
    assert await a.acquire("run_1") is True
    assert await a.acquire("run_1") is True


async def test_leases_are_per_run(make: Make) -> None:
    a, b = make("a", 60), make("b", 60)
    assert await a.acquire("run_1") is True
    assert await b.acquire("run_2") is True


async def test_a_released_lease_can_be_taken_over(make: Make) -> None:
    a, b = make("a", 60), make("b", 60)
    await a.acquire("run_1")
    await a.release("run_1")
    assert await b.acquire("run_1") is True


async def test_only_the_owner_can_release(make: Make) -> None:
    a, b = make("a", 60), make("b", 60)
    await a.acquire("run_1")
    await b.release("run_1")  # not b's: a no-op
    assert await b.acquire("run_1") is False
    assert await a.renew("run_1") is True


async def test_renew_succeeds_for_the_owner_and_fails_after_losing_the_lease(
    make: Make, request: pytest.FixtureRequest
) -> None:
    a, b = make("a", 1), make("b", 1)
    assert await a.renew("run_1") is False  # never acquired
    await a.acquire("run_1")
    assert await a.renew("run_1") is True
    if isinstance(a, InMemoryLeases):
        request.node.clock.now += 2
    else:
        await asyncio.sleep(1.2)
    assert await b.acquire("run_1") is True  # the lease expired: b takes over
    assert await a.renew("run_1") is False  # a learns it lost the run


async def test_an_expired_lease_is_free(make: Make, request: pytest.FixtureRequest) -> None:
    a, b = make("a", 1), make("b", 1)
    await a.acquire("run_1")
    if isinstance(a, InMemoryLeases):
        request.node.clock.now += 1.5
    else:
        await asyncio.sleep(1.2)
    assert await b.acquire("run_1") is True


async def test_fifty_replicas_racing_for_one_run_produce_one_winner(make: Make) -> None:
    replicas = [make(f"r{n}", 60) for n in range(50)]
    results = await asyncio.gather(*(r.acquire("run_1") for r in replicas))
    assert sum(results) == 1


async def test_many_runs_split_between_two_replicas_never_overlap(make: Make) -> None:
    a, b = make("a", 60), make("b", 60)
    runs = [f"run_{n}" for n in range(40)]
    got_a, got_b = await asyncio.gather(
        asyncio.gather(*(a.acquire(r) for r in runs)), asyncio.gather(*(b.acquire(r) for r in runs))
    )
    assert all(x != y for x, y in zip(got_a, got_b, strict=True))  # each run has exactly one owner


def test_a_lease_needs_a_positive_ttl_and_an_owner() -> None:
    with pytest.raises(ValueError):
        InMemoryLeases({}, "a", ttl_s=0)
    with pytest.raises(ValueError):
        InMemoryLeases({}, "", ttl_s=5)
    fake: Any = object()
    with pytest.raises(ValueError):
        RedisLeases(fake, "a", ttl_s=0)
    with pytest.raises(ValueError):
        RedisLeases(fake, "", ttl_s=5)
