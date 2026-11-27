"""Verdict cache on Redis. Keys carry every version (`common.cache_keys`); all keys expire."""

import json

from prometheus_client import Counter
from redis.asyncio import Redis

from ..models.storage import CacheResult

PREFIX = "verdict:"


class CacheService:
    def __init__(self, redis: Redis, default_ttl_s: int, hits: Counter, misses: Counter) -> None:
        self._redis = redis
        self._ttl = default_ttl_s
        self._hits = hits
        self._misses = misses

    async def get(self, key: str) -> CacheResult:
        raw = await self._redis.get(PREFIX + key)
        if raw is None:
            self._misses.labels(cache="verdict").inc()
            return CacheResult(hit=False)
        self._hits.labels(cache="verdict").inc()
        return CacheResult(hit=True, value=json.loads(raw))

    async def put(self, key: str, value: dict[str, object], ttl_s: int | None) -> None:
        await self._redis.set(
            PREFIX + key, json.dumps(value, sort_keys=True), ex=ttl_s or self._ttl
        )

    async def ping(self) -> bool:
        return bool(await self._redis.ping())
