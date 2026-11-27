"""Recovery attempt counters: a run that keeps killing its process is given up on."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from redis.asyncio import Redis

from svc_orchestrator.repositories.attempts import (
    InMemoryAttempts,
    RecoveryAttempts,
    RedisAttempts,
    attempts_key,
)

Make = Callable[[], RecoveryAttempts]


@pytest.fixture(params=["memory", pytest.param("real", marks=pytest.mark.integration)])
async def make(request: pytest.FixtureRequest) -> AsyncIterator[Make]:
    if request.param == "memory":
        table: dict[str, int] = {}
        yield lambda: InMemoryAttempts(table)
        return
    client = Redis.from_url(request.getfixturevalue("redis_url"))
    await client.flushdb()
    yield lambda: RedisAttempts(client)
    await client.aclose()


async def test_each_restore_counts_one_more(make: Make) -> None:
    attempts = make()
    assert [await attempts.incr("run_1") for _ in range(3)] == [1, 2, 3]


async def test_runs_are_counted_separately(make: Make) -> None:
    attempts = make()
    await attempts.incr("run_1")
    assert await attempts.incr("run_2") == 1


async def test_a_reset_starts_the_count_again(make: Make) -> None:
    attempts = make()
    await attempts.incr("run_1")
    await attempts.incr("run_1")
    await attempts.reset("run_1")
    assert await attempts.incr("run_1") == 1
    await attempts.reset("never_counted")


async def test_the_count_is_shared_between_replicas_and_atomic(make: Make) -> None:
    a, b = make(), make()
    counts = await asyncio.gather(*(x.incr("run_1") for x in (a, b) for _ in range(10)))
    assert sorted(counts) == list(range(1, 21))


async def test_the_redis_counter_expires() -> None:
    class Spy:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def incr(self, key: str) -> int:
            self.calls.append(("incr", key))
            return 1

        async def expire(self, key: str, seconds: int) -> None:
            self.calls.append(("expire", key, seconds))

        async def delete(self, key: str) -> None: ...

    spy = Spy()
    await RedisAttempts(spy).incr("run_1")  # type: ignore[arg-type]
    assert spy.calls[0] == ("incr", attempts_key("run_1"))
    assert spy.calls[1][0] == "expire" and spy.calls[1][2] > 0
