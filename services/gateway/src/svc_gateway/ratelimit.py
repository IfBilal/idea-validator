"""Fixed-window rate limits shared by every gateway replica through Redis (docs/harness/04 §3)."""

import hashlib
import time
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis

from common.errors import RateLimited

WINDOW_S = 60


def fingerprint(value: str) -> str:
    """Keys must not hold raw emails: hash the identifier."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]


class RateLimiter(Protocol):
    async def hit(self, kind: str, ident: str, limit: int) -> None:
        """Count one request; raise ``RateLimited`` past ``limit`` per window."""


class RedisRateLimiter:
    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, kind: str, ident: str, limit: int) -> None:
        now = self._clock()
        window = int(now // WINDOW_S)
        key = f"ratelimit:{kind}:{ident}:{window}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, WINDOW_S + 1)
        if count > limit:
            retry_after = max(1, int((window + 1) * WINDOW_S - now))
            raise RateLimited("too many requests", {"retry_after": retry_after, "limit": limit})


class MemoryRateLimiter:
    """In-process limiter for tests and local development (not shared between replicas)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counts: dict[str, int] = {}

    async def hit(self, kind: str, ident: str, limit: int) -> None:
        now = self._clock()
        window = int(now // WINDOW_S)
        key = f"{kind}:{ident}:{window}"
        self._counts = {k: v for k, v in self._counts.items() if k.endswith(f":{window}")}
        self._counts[key] = self._counts.get(key, 0) + 1
        if self._counts[key] > limit:
            retry_after = max(1, int((window + 1) * WINDOW_S - now))
            raise RateLimited("too many requests", {"retry_after": retry_after, "limit": limit})
