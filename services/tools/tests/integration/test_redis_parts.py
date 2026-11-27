import asyncio

import pytest
from redis.asyncio import Redis

from common.errors import RateLimited, UpstreamError
from svc_tools.services.cache import RedisCache
from svc_tools.services.ratelimit import RedisTokenBucket

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str):  # type: ignore[no-untyped-def]
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


async def test_the_lua_bucket_allows_the_burst_then_blocks_with_retry_after(redis: Redis) -> None:
    bucket = RedisTokenBucket(redis, lambda: 1000.0)
    for _ in range(6):
        await bucket.acquire("api.github.com", 6)
    with pytest.raises(RateLimited) as blocked:
        await bucket.acquire("api.github.com", 6)
    assert blocked.value.details["retry_after"] == 10


async def test_the_lua_bucket_refills_over_time_and_caps_at_capacity(redis: Redis) -> None:
    clock = {"now": 0.0}
    bucket = RedisTokenBucket(redis, lambda: clock["now"])
    for _ in range(60):
        await bucket.acquire("h", 60)
    clock["now"] = 0.99
    with pytest.raises(RateLimited):
        await bucket.acquire("h", 60)
    clock["now"] = 1.0
    await bucket.acquire("h", 60)
    clock["now"] = 1_000_000.0
    for _ in range(60):
        await bucket.acquire("h", 60)
    with pytest.raises(RateLimited):
        await bucket.acquire("h", 60)


async def test_concurrent_acquires_never_exceed_the_rate(redis: Redis) -> None:
    bucket = RedisTokenBucket(redis, lambda: 5000.0)
    results = await asyncio.gather(
        *(bucket.acquire("burst", 20) for _ in range(100)), return_exceptions=True
    )
    assert sum(r is None for r in results) == 20
    assert sum(isinstance(r, RateLimited) for r in results) == 80


async def test_two_replicas_share_one_bucket_and_every_key_expires(
    redis_url: str, redis: Redis
) -> None:
    other = Redis.from_url(redis_url)
    a, b = RedisTokenBucket(redis, lambda: 9000.0), RedisTokenBucket(other, lambda: 9000.0)
    await a.acquire("shared", 2)
    await b.acquire("shared", 2)
    with pytest.raises(RateLimited):
        await a.acquire("shared", 2)
    assert 0 < await redis.ttl("ratelimit:domain:shared") <= 3600
    await other.aclose()


async def test_the_cache_stores_json_with_a_ttl_and_ignores_non_positive_ttls(redis: Redis) -> None:
    cache = RedisCache(redis)
    assert await cache.get("fetch:k") is None
    await cache.set("fetch:k", {"a": [1, {"b": None}]}, ttl_s=60)
    assert await cache.get("fetch:k") == {"a": [1, {"b": None}]}
    assert 0 < await redis.ttl("fetch:k") <= 60
    await cache.set("fetch:zero", {"a": 1}, ttl_s=0)
    assert await cache.get("fetch:zero") is None


async def test_a_dead_redis_is_an_upstream_error_not_a_raw_exception() -> None:
    dead = Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.2)
    with pytest.raises(UpstreamError):
        await RedisTokenBucket(dead).acquire("h", 5)
    with pytest.raises(UpstreamError):
        await RedisCache(dead).get("k")
    await dead.aclose()
