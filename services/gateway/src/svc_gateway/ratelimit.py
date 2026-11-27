"""Fixed-window rate limits shared by every gateway replica through Redis (docs/harness/04 §3).

If Redis is unreachable the limiter fails closed with 503: a gateway that cannot count attempts
must not let brute-force traffic through.
"""

import hashlib
import time
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from common.errors import RateLimited, Unavailable

WINDOW_S = 60


def fingerprint(value: str) -> str:
    """Keys must not hold raw emails or IPs of users: hash the identifier."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]


def _blocked(limit: int, window: int, now: float) -> RateLimited:
    retry_after = max(1, int((window + 1) * WINDOW_S - now))
    return RateLimited("too many requests", {"retry_after": retry_after, "limit": limit})


class RateLimiter(Protocol):
    async def hit(self, kind: str, ident: str, limit: int) -> None:
        """Count one event; raise ``RateLimited`` once more than ``limit`` happened this window."""

    async def peek(self, kind: str, ident: str, limit: int) -> None:
        """Raise ``RateLimited`` if ``limit`` events already happened this window (no counting)."""


class RedisRateLimiter:
    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._clock = clock

    def _key(self, kind: str, ident: str) -> tuple[str, int, float]:
        now = self._clock()
        window = int(now // WINDOW_S)
        return f"ratelimit:{kind}:{ident}:{window}", window, now

    async def hit(self, kind: str, ident: str, limit: int) -> None:
        key, window, now = self._key(kind, ident)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, WINDOW_S + 1)  # always set: a lost EXPIRE can never leak a key
                count, _ = await pipe.execute()
        except RedisError as exc:
            raise Unavailable("rate limiter unavailable") from exc
        if count > limit:
            raise _blocked(limit, window, now)

    async def peek(self, kind: str, ident: str, limit: int) -> None:
        key, window, now = self._key(kind, ident)
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            raise Unavailable("rate limiter unavailable") from exc
        if raw is not None and int(raw) >= limit:
            raise _blocked(limit, window, now)


class MemoryRateLimiter:
    """In-process limiter for tests and local development (not shared between replicas)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counts: dict[str, int] = {}

    def _key(self, kind: str, ident: str) -> tuple[str, int, float]:
        now = self._clock()
        window = int(now // WINDOW_S)
        self._counts = {k: v for k, v in self._counts.items() if k.endswith(f":{window}")}
        return f"{kind}:{ident}:{window}", window, now

    async def hit(self, kind: str, ident: str, limit: int) -> None:
        key, window, now = self._key(kind, ident)
        self._counts[key] = self._counts.get(key, 0) + 1
        if self._counts[key] > limit:
            raise _blocked(limit, window, now)

    async def peek(self, kind: str, ident: str, limit: int) -> None:
        key, window, now = self._key(kind, ident)
        if self._counts.get(key, 0) >= limit:
            raise _blocked(limit, window, now)
