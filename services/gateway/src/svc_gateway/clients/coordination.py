"""Small shared state in Redis: idempotency records for run starts and open-stream counters.

Both fail closed (``Unavailable`` -> 503): a gateway that cannot tell whether a request is a retry,
or how many streams a user holds, must not guess.
"""

import json
from typing import Any, Protocol

from redis.exceptions import RedisError

from common.errors import Unavailable

PENDING: dict[str, Any] = {"pending": True}
PENDING_TTL_S = 60


class IdempotencyStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def claim(self, key: str, ttl_s: int) -> bool:
        """Reserve ``key`` (atomically); ``False`` if somebody already holds or finished it."""

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None: ...

    async def delete(self, key: str) -> None: ...


class StreamGate(Protocol):
    async def acquire(self, user_id: str, limit: int, ttl_s: int) -> bool:
        """Take one stream slot; ``False`` (and nothing taken) if the user is at ``limit``."""

    async def release(self, user_id: str) -> None: ...


def _unavailable(exc: Exception) -> Unavailable:
    return Unavailable("a required service is unavailable")


class RedisIdempotencyStore:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(f"idem:{key}")
        except RedisError as exc:
            raise _unavailable(exc) from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    async def claim(self, key: str, ttl_s: int) -> bool:
        try:
            return bool(
                await self._redis.set(f"idem:{key}", json.dumps(PENDING), ex=ttl_s, nx=True)
            )
        except RedisError as exc:
            raise _unavailable(exc) from exc

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        try:
            await self._redis.set(f"idem:{key}", json.dumps(value), ex=ttl_s)
        except RedisError as exc:
            raise _unavailable(exc) from exc

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(f"idem:{key}")
        except RedisError as exc:
            raise _unavailable(exc) from exc


class RedisStreamGate:
    """A counter per user with a TTL, so a crashed replica cannot hold slots forever."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def acquire(self, user_id: str, limit: int, ttl_s: int) -> bool:
        key = f"stream:{user_id}"
        try:
            count = int(await self._redis.incr(key))
            await self._redis.expire(key, ttl_s)
            if count > limit:
                await self._redis.decr(key)
                return False
        except RedisError as exc:
            raise _unavailable(exc) from exc
        return True

    async def release(self, user_id: str) -> None:
        key = f"stream:{user_id}"
        try:
            if int(await self._redis.decr(key)) < 0:
                await self._redis.set(key, "0", ex=60)
        except RedisError as exc:
            raise _unavailable(exc) from exc


class MemoryIdempotencyStore:
    """In-process twin for tests and local development."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.broken = False

    def _check(self) -> None:
        if self.broken:
            raise Unavailable("a required service is unavailable")

    async def get(self, key: str) -> dict[str, Any] | None:
        self._check()
        return self.values.get(key)

    async def claim(self, key: str, ttl_s: int) -> bool:
        self._check()
        if key in self.values:
            return False
        self.values[key] = dict(PENDING)
        return True

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        self._check()
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self._check()
        self.values.pop(key, None)


class MemoryStreamGate:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self.broken = False

    def count(self, user_id: str) -> int:
        return self._counts.get(user_id, 0)

    def acquire_sync(self, user_id: str, limit: int) -> bool:
        if self.count(user_id) >= limit:
            return False
        self._counts[user_id] = self.count(user_id) + 1
        return True

    def release_sync(self, user_id: str) -> None:
        self._counts[user_id] = max(0, self.count(user_id) - 1)

    async def acquire(self, user_id: str, limit: int, ttl_s: int) -> bool:
        if self.broken:
            raise Unavailable("a required service is unavailable")
        return self.acquire_sync(user_id, limit)

    async def release(self, user_id: str) -> None:
        self.release_sync(user_id)
