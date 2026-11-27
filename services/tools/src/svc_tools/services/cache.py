"""Fetch cache (Redis, every entry has a TTL). The value is already sanitised."""

import json
import time
from collections.abc import Callable
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from common.errors import UpstreamError


class KeyValueCache(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None: ...


class MemoryCache:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        entry = self._items.get(key)
        if entry is None or entry[0] <= self._clock():
            self._items.pop(key, None)
            return None
        return entry[1]

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        if ttl_s > 0:
            self._items[key] = (self._clock() + ttl_s, value)


class RedisCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            raise UpstreamError("cache unavailable") from exc
        if raw is None:
            return None
        value: dict[str, Any] = json.loads(raw)
        return value

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        if ttl_s <= 0:
            return
        try:
            await self._redis.set(key, json.dumps(value, sort_keys=True), ex=ttl_s)
        except RedisError as exc:
            raise UpstreamError("cache unavailable") from exc
