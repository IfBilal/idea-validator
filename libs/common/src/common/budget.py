"""Per-run budget ledger shared by M3 and M5 (docs/harness/07 §6, 12).

The ledger is a Redis hash ``budget:{run_id}`` with the fields ``tokens``, ``pages``, ``steps``,
``cost_usd`` (and ``cost_bonus_usd``, the one-time raise granted by an approval). Every update is a
single atomic ``HINCRBY``/``HINCRBYFLOAT``, so M3 and M5 can write the same hash concurrently.
Limits come from ``config/budgets.yaml`` per run kind. Spending exactly the limit is allowed;
going over it is not.

The module needs no Redis client library: ``RedisLike`` is the handful of async hash commands used,
which ``redis.asyncio.Redis`` satisfies.
"""

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from .config import ConfigError, load_yaml_config
from .errors import BudgetExceeded
from .schemas.base import Message

DEFAULT_TTL_S = 7 * 24 * 3600
_COST_TOLERANCE = 1e-9  # HINCRBYFLOAT accumulates binary float noise (0.01 * 5 != 0.05)
_FIELDS = ("tokens", "pages", "steps", "cost_usd")

# Check and charge in ONE atomic Redis step. A read followed by a separate write lets parallel steps
# all pass the check and overshoot (max_steps=4 ended at 7 with four parallel searches). Only
# ``steps`` is charged here: pages, tokens and cost are charged by M5 into the same hash, so
# counting them again would double-bill.
# ARGV: est tokens/pages/steps/cost, max tokens/pages/steps/cost, tolerance, ttl.
RESERVE_SCRIPT = """
local function n(field) return tonumber(redis.call('HGET', KEYS[1], field) or '0') or 0 end
local over = {}
if n('tokens') + tonumber(ARGV[1]) > tonumber(ARGV[5]) then over[#over + 1] = 'tokens' end
if n('pages') + tonumber(ARGV[2]) > tonumber(ARGV[6]) then over[#over + 1] = 'pages' end
if n('steps') + tonumber(ARGV[3]) > tonumber(ARGV[7]) then over[#over + 1] = 'steps' end
local cap = tonumber(ARGV[8]) + n('cost_bonus_usd') + tonumber(ARGV[9])
if n('cost_usd') + tonumber(ARGV[4]) > cap then over[#over + 1] = 'cost_usd' end
if #over > 0 then return over end
if tonumber(ARGV[3]) > 0 then redis.call('HINCRBY', KEYS[1], 'steps', ARGV[3]) end
redis.call('EXPIRE', KEYS[1], ARGV[10])
return over
"""


# Restore only into a hash that does not exist: the check and the write are one atomic step.
RESTORE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'tokens', ARGV[1], 'pages', ARGV[2], 'steps', ARGV[3],
           'cost_usd', ARGV[4], 'cost_bonus_usd', ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[6])
return 1
"""


@dataclass(frozen=True)
class StepClaim:
    free: bool  # a re-run of a step that was already charged: do not charge again
    reruns: int  # how many times this step's claim was taken over after a crash


class RedisLike(Protocol):
    def hincrby(self, name: str, key: str, amount: int) -> Awaitable[Any]: ...
    def hincrbyfloat(self, name: str, key: str, amount: float) -> Awaitable[Any]: ...
    def hgetall(self, name: str) -> Awaitable[Any]: ...
    def expire(self, name: str, time: int) -> Awaitable[Any]: ...
    def ttl(self, name: str) -> Awaitable[Any]: ...
    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Awaitable[Any]: ...
    def set(
        self, name: str, value: str, *, ex: int | None = None, get: bool = False
    ) -> Awaitable[Any]: ...
    def delete(self, *names: str) -> Awaitable[Any]: ...
    def get(self, name: str) -> Awaitable[Any]: ...
    def incr(self, name: str) -> Awaitable[Any]: ...


class Spend(Message):
    """An amount of spend: either what was used, or an estimate for a step about to run."""

    tokens: int = Field(default=0, ge=0)
    pages: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)


class BudgetLimits(Message):
    max_tokens: int = Field(ge=0)
    max_pages: int = Field(ge=0)
    max_steps: int = Field(ge=0)
    max_cost_usd: float = Field(ge=0)
    approval_threshold_usd: float | None = Field(default=None, ge=0)


def limits_from_config(config: Mapping[str, Any], kind: str) -> BudgetLimits:
    """Read one run kind (``validate``, ``discover`` ...) out of the parsed budgets.yaml."""
    section = config.get(kind)
    if not isinstance(section, Mapping):
        raise ConfigError(f"budgets config has no run kind {kind!r}")
    missing = [
        f for f in ("max_tokens", "max_pages", "max_steps", "max_cost_usd") if f not in section
    ]
    if missing:
        raise ConfigError(f"budgets config for {kind!r} is missing {', '.join(missing)}")
    return BudgetLimits(
        max_tokens=section["max_tokens"],
        max_pages=section["max_pages"],
        max_steps=section["max_steps"],
        max_cost_usd=section["max_cost_usd"],
        approval_threshold_usd=section.get("approval_threshold_usd"),
    )


def load_limits(kind: str, *, config_dir: Path | None = None) -> BudgetLimits:
    return limits_from_config(load_yaml_config("budgets", config_dir=config_dir), kind)


def _num(raw: Mapping[Any, Any], key: str) -> float:
    for k, v in raw.items():
        if (k.decode() if isinstance(k, bytes) else k) == key:
            return float(v)
    return 0.0


class BudgetLedger:
    def __init__(
        self,
        redis: RedisLike,
        run_id: str,
        limits: BudgetLimits,
        *,
        ttl_s: int = DEFAULT_TTL_S,
    ) -> None:
        self._redis = redis
        self.run_id = run_id
        self._base_limits = limits
        self._ttl_s = ttl_s
        self._key = f"budget:{run_id}"

    async def record(self, spend: Spend) -> Spend:
        """Add ``spend`` atomically field by field; returns the new totals."""
        for field in ("tokens", "pages", "steps"):
            if amount := getattr(spend, field):
                await self._redis.hincrby(self._key, field, amount)
        if spend.cost_usd:
            await self._redis.hincrbyfloat(self._key, "cost_usd", spend.cost_usd)
        await self._redis.expire(self._key, self._ttl_s)
        return await self.spent()

    async def claim_step(self, token: str, owner: str) -> "StepClaim":
        """Mark a step as in flight and say whether it is a re-run that must not be charged.

        A step running when its process died is re-run after recovery. If it had already been
        charged (``mark_paid``) the re-run is ``free``; if the process died between the claim and
        the charge it is not, so a crash can never leave a free step behind. ``reruns`` counts the
        takeovers of the claim so a poison step can be capped by the caller. A claim by the same
        ``owner`` is a second identical step in flight at once: real work, billed normally.
        The caller ``release_step``s on completion or error, but not when it is cancelled.
        """
        previous = await self._redis.set(self._claim_key(token), owner, ex=self._ttl_s, get=True)
        text = previous.decode() if isinstance(previous, bytes) else previous
        if text is None or text == owner:
            return StepClaim(free=False, reruns=0)
        key = f"{self._claim_key(token)}:reruns"
        reruns = int(await self._redis.incr(key))
        await self._redis.expire(key, self._ttl_s)
        paid = await self._redis.get(f"{self._claim_key(token)}:paid") is not None
        return StepClaim(free=paid, reruns=reruns)

    async def mark_paid(self, token: str) -> None:
        """The step has been charged: a re-run after a crash is free."""
        await self._redis.set(f"{self._claim_key(token)}:paid", "1", ex=self._ttl_s)

    async def release_step(self, token: str) -> None:
        base = self._claim_key(token)
        await self._redis.delete(base, f"{base}:paid", f"{base}:reruns")

    def _claim_key(self, token: str) -> str:
        return f"{self._key}:claim:{token}"

    async def restore_if_empty(self, spent: Spend, cost_bonus_usd: float = 0.0) -> bool:
        """Put back the spend and cost raise mirrored to M2 after Redis lost them.

        One atomic step: it never overwrites a hash that exists, so M5 charges made since are kept.
        """
        if cost_bonus_usd < 0:
            raise ValueError("a cost raise cannot be negative")
        if spent == Spend() and cost_bonus_usd == 0:
            return False
        done = await self._redis.eval(
            RESTORE_SCRIPT,
            1,
            self._key,
            spent.tokens,
            spent.pages,
            spent.steps,
            repr(spent.cost_usd),
            repr(cost_bonus_usd),
            self._ttl_s,
        )
        return int(done) == 1

    async def reserve(self, estimate: Spend) -> Spend:
        """Atomically refuse (``BudgetExceeded``, nothing changed) or admit a step and count it.

        Replaces ``exceeded_fields`` + ``record`` for a step about to run: with parallel steps the
        old pair could admit more than the limit. Returns the new totals.
        """
        lim = self._base_limits
        raw = await self._redis.eval(
            RESERVE_SCRIPT,
            1,
            self._key,
            estimate.tokens,
            estimate.pages,
            estimate.steps,
            repr(estimate.cost_usd),
            lim.max_tokens,
            lim.max_pages,
            lim.max_steps,
            repr(lim.max_cost_usd),
            repr(_COST_TOLERANCE),
            self._ttl_s,
        )
        over = [f.decode() if isinstance(f, bytes) else str(f) for f in (raw or [])]
        spent = await self.spent()
        if over:
            raise BudgetExceeded(
                f"run {self.run_id} would exceed its budget: {', '.join(over)}",
                {
                    "exceeded": over,
                    "spent": spent.model_dump(),
                    "limits": (await self.limits()).model_dump(),
                    "estimate": estimate.model_dump(),
                },
            )
        return spent

    async def spent(self) -> Spend:
        raw: Mapping[Any, Any] = await self._redis.hgetall(self._key)
        return Spend(
            tokens=int(_num(raw, "tokens")),
            pages=int(_num(raw, "pages")),
            steps=int(_num(raw, "steps")),
            cost_usd=max(0.0, _num(raw, "cost_usd")),
        )

    async def limits(self) -> BudgetLimits:
        """Configured limits plus any one-time cost raise granted by an approval."""
        raw: Mapping[Any, Any] = await self._redis.hgetall(self._key)
        bonus = _num(raw, "cost_bonus_usd")
        return self._base_limits.model_copy(
            update={"max_cost_usd": self._base_limits.max_cost_usd + bonus}
        )

    async def raise_cost_limit(self, amount_usd: float) -> None:
        """An approved step may spend ``amount_usd`` more than the configured cap."""
        if amount_usd < 0:
            raise ValueError("a cost raise cannot be negative")
        await self._redis.hincrbyfloat(self._key, "cost_bonus_usd", amount_usd)
        await self._redis.expire(self._key, self._ttl_s)

    async def exceeded_fields(self, estimate: Spend | None = None) -> list[str]:
        """Fields where ``spent + estimate`` is strictly over the limit."""
        spent, limits = await self.spent(), await self.limits()
        extra = estimate or Spend()
        over: list[str] = []
        if spent.tokens + extra.tokens > limits.max_tokens:
            over.append("tokens")
        if spent.pages + extra.pages > limits.max_pages:
            over.append("pages")
        if spent.steps + extra.steps > limits.max_steps:
            over.append("steps")
        if spent.cost_usd + extra.cost_usd > limits.max_cost_usd + _COST_TOLERANCE:
            over.append("cost_usd")
        return over

    async def would_exceed(self, estimate: Spend) -> bool:
        return bool(await self.exceeded_fields(estimate))

    async def check(self) -> None:
        """Raise ``BudgetExceeded`` if the run is already over any limit."""
        if over := await self.exceeded_fields():
            spent, limits = await self.spent(), await self.limits()
            raise BudgetExceeded(
                f"run {self.run_id} exceeded its budget: {', '.join(over)}",
                {
                    "exceeded": over,
                    "spent": spent.model_dump(),
                    "limits": limits.model_dump(),
                },
            )

    async def ttl_s(self) -> int:
        """Remaining TTL of the hash in seconds."""
        return int(await self._redis.ttl(self._key))


__all__ = [
    "DEFAULT_TTL_S",
    "RESTORE_SCRIPT",
    "BudgetLedger",
    "BudgetLimits",
    "RedisLike",
    "Spend",
    "StepClaim",
    "limits_from_config",
    "load_limits",
]
