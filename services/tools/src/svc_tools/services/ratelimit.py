"""Per-domain token buckets (docs/harness/10 §3): burst up to the rate, refill smoothly."""

import math
import time
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from common.errors import RateLimited, UpstreamError

# Atomic refill-and-take. KEYS[1]=bucket; ARGV: rate_per_min, now (seconds, float), cost
_LUA = """
local rate = tonumber(ARGV[1]) / 60.0
local capacity = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local wait = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  if rate > 0 then wait = math.ceil((cost - tokens) / rate) else wait = 3600 end
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], 3600)
return {allowed, wait}
"""


class TokenBucket(Protocol):
    async def acquire(self, domain: str, rate_per_min: int) -> None:
        """Take one token or raise ``RateLimited`` with ``retry_after`` seconds."""


def _limited(domain: str, wait: int) -> RateLimited:
    return RateLimited("domain rate limit reached", {"retry_after": max(1, wait), "domain": domain})


class MemoryTokenBucket:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._state: dict[str, tuple[float, float]] = {}

    async def acquire(self, domain: str, rate_per_min: int) -> None:
        now = self._clock()
        capacity = float(rate_per_min)
        rate = rate_per_min / 60.0
        tokens, stamp = self._state.get(domain, (capacity, now))
        tokens = min(capacity, tokens + max(0.0, now - stamp) * rate)
        if tokens >= 1:
            self._state[domain] = (tokens - 1, now)
            return
        self._state[domain] = (tokens, now)
        wait = math.ceil((1 - tokens) / rate) if rate > 0 else 3600
        raise _limited(domain, wait)


class RedisTokenBucket:
    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._clock = clock
        self._script = redis.register_script(_LUA)

    async def acquire(self, domain: str, rate_per_min: int) -> None:
        try:
            allowed, wait = await self._script(
                keys=[f"ratelimit:domain:{domain}"], args=[rate_per_min, self._clock(), 1]
            )
        except RedisError as exc:
            raise UpstreamError("rate limiter unavailable") from exc
        if not allowed:
            raise _limited(domain, int(wait))
