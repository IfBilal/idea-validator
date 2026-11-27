"""Run leases: one replica drives a run at a time (docs/harness/07 §1).

A run lives in the memory of the replica that started or recovered it. Without a lease, two replicas
recovering at once (a rolling update) would both resume the same run and spend its budget twice.
The lease is a Redis key ``run:{id}:lease`` holding the owner id with a short TTL; the owner renews
it while the run is active and releases it when the run ends. A crashed owner stops renewing, so its
runs become recoverable after at most one TTL.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

LEASE_TTL_S = 60

# SET NX, or refresh the TTL when the caller already owns the key. One atomic step.
_ACQUIRE = """
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then return 1 end
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""
_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


def lease_key(run_id: str) -> str:
    return f"run:{run_id}:lease"


class RunLeases(Protocol):
    async def acquire(self, run_id: str) -> bool: ...

    async def renew(self, run_id: str) -> bool: ...

    async def release(self, run_id: str) -> None: ...


def _check(owner: str, ttl_s: int) -> None:
    if not owner:
        raise ValueError("a lease needs an owner id")
    if ttl_s < 1:
        raise ValueError("a lease TTL must be at least one second")


class EvalRedis(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Awaitable[Any]: ...


class RedisLeases:
    def __init__(self, redis: EvalRedis, owner: str, *, ttl_s: int = LEASE_TTL_S) -> None:
        _check(owner, ttl_s)
        self._redis, self._owner, self._ttl_s = redis, owner, ttl_s

    async def acquire(self, run_id: str) -> bool:
        got = await self._redis.eval(_ACQUIRE, 1, lease_key(run_id), self._owner, self._ttl_s)
        return int(got) == 1

    async def renew(self, run_id: str) -> bool:
        got = await self._redis.eval(_RENEW, 1, lease_key(run_id), self._owner, self._ttl_s)
        return int(got) == 1

    async def release(self, run_id: str) -> None:
        await self._redis.eval(_RELEASE, 1, lease_key(run_id), self._owner)


class InMemoryLeases:
    """Same contract without Redis; instances sharing ``table`` act like replicas on one Redis."""

    def __init__(
        self,
        table: dict[str, tuple[str, float]],
        owner: str,
        *,
        ttl_s: int = LEASE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _check(owner, ttl_s)
        self._table, self._owner, self._ttl_s, self._clock = table, owner, ttl_s, clock

    def _holder(self, run_id: str) -> str | None:
        held = self._table.get(run_id)
        if held is None or held[1] <= self._clock():
            self._table.pop(run_id, None)
            return None
        return held[0]

    def _take(self, run_id: str) -> None:
        self._table[run_id] = (self._owner, self._clock() + self._ttl_s)

    async def acquire(self, run_id: str) -> bool:
        if self._holder(run_id) not in (None, self._owner):
            return False
        self._take(run_id)
        return True

    async def renew(self, run_id: str) -> bool:
        if self._holder(run_id) != self._owner:
            return False
        self._take(run_id)
        return True

    async def release(self, run_id: str) -> None:
        if self._holder(run_id) == self._owner:
            del self._table[run_id]
