"""How many times a run has been restored (docs/harness/07 §1).

A run that crashes the process every time it is resumed would otherwise be recovered, crash and be
recovered again for ever. Each restore counts; ``RunService`` gives up after a limit, and resets the
count once the run is safely paused or finished.
"""

from collections.abc import Awaitable
from typing import Any, Protocol

ATTEMPTS_TTL_S = 7 * 24 * 3600


def attempts_key(run_id: str) -> str:
    return f"run:{run_id}:recoveries"


class RecoveryAttempts(Protocol):
    async def incr(self, run_id: str) -> int: ...

    async def reset(self, run_id: str) -> None: ...


class AttemptsRedis(Protocol):
    def incr(self, name: str) -> Awaitable[Any]: ...
    def expire(self, name: str, time: int) -> Awaitable[Any]: ...
    def delete(self, *names: str) -> Awaitable[Any]: ...


class RedisAttempts:
    def __init__(self, redis: AttemptsRedis) -> None:
        self._redis = redis

    async def incr(self, run_id: str) -> int:
        key = attempts_key(run_id)
        count = int(await self._redis.incr(key))  # INCR is atomic across replicas
        await self._redis.expire(key, ATTEMPTS_TTL_S)
        return count

    async def reset(self, run_id: str) -> None:
        await self._redis.delete(attempts_key(run_id))


class InMemoryAttempts:
    def __init__(self, table: dict[str, int] | None = None) -> None:
        self._table = {} if table is None else table

    async def incr(self, run_id: str) -> int:
        self._table[run_id] = self._table.get(run_id, 0) + 1
        return self._table[run_id]

    async def reset(self, run_id: str) -> None:
        self._table.pop(run_id, None)
