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
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from .config import ConfigError, load_yaml_config
from .errors import BudgetExceeded
from .schemas.base import Message

DEFAULT_TTL_S = 7 * 24 * 3600
_COST_TOLERANCE = 1e-9  # HINCRBYFLOAT accumulates binary float noise (0.01 * 5 != 0.05)
_FIELDS = ("tokens", "pages", "steps", "cost_usd")


class RedisLike(Protocol):
    def hincrby(self, name: str, key: str, amount: int) -> Awaitable[Any]: ...
    def hincrbyfloat(self, name: str, key: str, amount: float) -> Awaitable[Any]: ...
    def hgetall(self, name: str) -> Awaitable[Any]: ...
    def expire(self, name: str, time: int) -> Awaitable[Any]: ...
    def ttl(self, name: str) -> Awaitable[Any]: ...


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
    "BudgetLedger",
    "BudgetLimits",
    "RedisLike",
    "Spend",
    "limits_from_config",
    "load_limits",
]
