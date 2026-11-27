import asyncio

import pytest
from redis.asyncio import Redis

from common.errors import RateLimited
from svc_gateway.ratelimit import RedisRateLimiter

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str):  # type: ignore[no-untyped-def]
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


async def test_the_limit_is_exact_and_keys_expire(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, lambda: 1000.0)
    for _ in range(3):
        await limiter.hit("login_ip", "1.2.3.4", 3)
    with pytest.raises(RateLimited):
        await limiter.hit("login_ip", "1.2.3.4", 3)
    keys = [k async for k in redis.scan_iter("ratelimit:*")]
    assert len(keys) == 1
    assert 0 < await redis.ttl(keys[0]) <= 61


async def test_a_new_window_starts_from_zero(redis: Redis) -> None:
    clock = {"now": 60.0}
    limiter = RedisRateLimiter(redis, lambda: clock["now"])
    await limiter.hit("k", "i", 1)
    with pytest.raises(RateLimited):
        await limiter.hit("k", "i", 1)
    clock["now"] = 120.0
    await limiter.hit("k", "i", 1)


async def test_concurrent_requests_cannot_exceed_the_limit(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, lambda: 5000.0)
    results = await asyncio.gather(
        *(limiter.hit("burst", "ip", 20) for _ in range(100)), return_exceptions=True
    )
    allowed = sum(1 for r in results if r is None)
    blocked = sum(1 for r in results if isinstance(r, RateLimited))
    assert (allowed, blocked) == (20, 80)


async def test_two_gateway_replicas_share_one_counter(redis_url: str, redis: Redis) -> None:
    other = Redis.from_url(redis_url)
    a, b = RedisRateLimiter(redis, lambda: 9000.0), RedisRateLimiter(other, lambda: 9000.0)
    await a.hit("k", "i", 2)
    await b.hit("k", "i", 2)
    with pytest.raises(RateLimited):
        await a.hit("k", "i", 2)
    await other.aclose()


async def test_peek_reads_without_counting_and_blocks_at_the_limit(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, lambda: 7000.0)
    for _ in range(5):
        await limiter.peek("fail", "e", 2)
    assert [k async for k in redis.scan_iter("ratelimit:*")] == []  # peeking creates nothing
    await limiter.hit("fail", "e", 2)
    await limiter.hit("fail", "e", 2)
    with pytest.raises(RateLimited):
        await limiter.peek("fail", "e", 2)


async def test_every_hit_refreshes_the_ttl_so_no_key_can_be_left_without_one(redis: Redis) -> None:
    limiter = RedisRateLimiter(redis, lambda: 8000.0)
    await limiter.hit("k", "i", 10)
    (key,) = [k async for k in redis.scan_iter("ratelimit:*")]
    await redis.persist(key)  # simulate a lost EXPIRE
    assert await redis.ttl(key) == -1
    await limiter.hit("k", "i", 10)
    assert 0 < await redis.ttl(key) <= 61


async def test_a_dead_redis_raises_unavailable_not_a_raw_connection_error() -> None:
    from common.errors import Unavailable

    dead = Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.2)
    limiter = RedisRateLimiter(dead)
    with pytest.raises(Unavailable):
        await limiter.hit("k", "i", 1)
    with pytest.raises(Unavailable):
        await limiter.peek("k", "i", 1)
    await dead.aclose()
