"""BudgetLedger: unit tests on an in-memory fake, plus the same contract against real Redis."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml

from common.budget import (
    DEFAULT_TTL_S,
    BudgetLedger,
    BudgetLimits,
    Spend,
    limits_from_config,
    load_limits,
)
from common.config import ConfigError
from common.errors import BudgetExceeded


class FakeRedis:
    """The few async Redis hash commands the ledger uses, in memory."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}

    async def hincrby(self, name: str, key: str, amount: int) -> int:
        h = self.hashes.setdefault(name, {})
        h[key] = int(h.get(key, 0)) + amount
        return int(h[key])

    async def hincrbyfloat(self, name: str, key: str, amount: float) -> float:
        h = self.hashes.setdefault(name, {})
        h[key] = float(h.get(key, 0.0)) + amount
        return float(h[key])

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        return {k.encode(): str(v).encode() for k, v in self.hashes.get(name, {}).items()}

    async def expire(self, name: str, seconds: int) -> bool:
        self.ttls[name] = seconds
        return name in self.hashes

    async def ttl(self, name: str) -> int:
        return self.ttls.get(name, -1)


LIMITS = BudgetLimits(max_tokens=100, max_pages=10, max_steps=5, max_cost_usd=0.05)


def ledger(redis: FakeRedis | None = None, run_id: str = "run_1") -> BudgetLedger:
    return BudgetLedger(redis or FakeRedis(), run_id, LIMITS)


# ---- limits from config --------------------------------------------------------------------
BUDGETS = {
    "validate": {
        "max_tokens": 60000,
        "max_pages": 30,
        "max_steps": 80,
        "max_cost_usd": 0.05,
        "approval_threshold_usd": 0.05,
    },
    "challenge": {
        "max_tokens": 10000,
        "max_pages": 0,
        "max_steps": 20,
        "max_cost_usd": 0.01,
        "approval_threshold_usd": 0.01,
    },
}


def test_limits_from_config_reads_the_run_kind() -> None:
    limits = limits_from_config(BUDGETS, "validate")
    assert limits == BudgetLimits(
        max_tokens=60000,
        max_pages=30,
        max_steps=80,
        max_cost_usd=0.05,
        approval_threshold_usd=0.05,
    )


def test_limits_from_config_unknown_kind_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="nope"):
        limits_from_config(BUDGETS, "nope")


def test_limits_from_config_missing_field_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="max_steps"):
        limits_from_config({"validate": {"max_tokens": 1}}, "validate")


def test_limits_reject_negative_values() -> None:
    with pytest.raises(ValueError):
        BudgetLimits(max_tokens=-1, max_pages=0, max_steps=0, max_cost_usd=0)


def test_load_limits_reads_the_yaml_file(tmp_path: Path) -> None:
    (tmp_path / "budgets.yaml").write_text(yaml.safe_dump(BUDGETS))
    assert load_limits("challenge", config_dir=tmp_path).max_pages == 0


def test_the_repo_budgets_file_has_every_run_kind() -> None:
    repo_config = Path(__file__).resolve().parents[3] / "config"
    for kind in ("validate", "discover", "whatif", "challenge", "eval"):
        assert load_limits(kind, config_dir=repo_config).max_steps > 0


# ---- recording and reading -----------------------------------------------------------------
async def test_a_new_run_has_zero_spend() -> None:
    assert await ledger().spent() == Spend()


async def test_record_accumulates_every_field() -> None:
    led = ledger()
    await led.record(Spend(tokens=10, pages=1, steps=1, cost_usd=0.01))
    after = await led.record(Spend(tokens=5, pages=2, steps=1, cost_usd=0.005))
    assert after == Spend(tokens=15, pages=3, steps=2, cost_usd=0.015)
    assert await led.spent() == after


async def test_record_writes_the_documented_key_and_ttl() -> None:
    redis = FakeRedis()
    await ledger(redis, "run_42").record(Spend(steps=1))
    assert "budget:run_42" in redis.hashes
    assert redis.ttls["budget:run_42"] == DEFAULT_TTL_S == 7 * 24 * 3600


async def test_runs_do_not_share_spend() -> None:
    redis = FakeRedis()
    await ledger(redis, "a").record(Spend(steps=3))
    assert (await ledger(redis, "b").spent()).steps == 0


def test_spend_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        Spend(tokens=-1)


async def test_concurrent_records_are_not_lost() -> None:
    led = ledger()
    await asyncio.gather(*(led.record(Spend(steps=1, tokens=2)) for _ in range(50)))
    assert await led.spent() == Spend(steps=50, tokens=100)


async def test_spent_reads_what_another_service_wrote() -> None:
    redis = FakeRedis()  # M5 mirrors tokens/pages/cost into the same hash
    await ledger(redis).record(Spend(tokens=7, pages=1, cost_usd=0.002))
    assert await ledger(redis).spent() == Spend(tokens=7, pages=1, cost_usd=0.002)


# ---- check(): exactly at the limit is fine, one over raises --------------------------------
@pytest.mark.parametrize(
    ("spend", "field"),
    [
        (Spend(tokens=101), "tokens"),
        (Spend(pages=11), "pages"),
        (Spend(steps=6), "steps"),
        (Spend(cost_usd=0.0501), "cost_usd"),
    ],
)
async def test_check_raises_one_over_the_limit(spend: Spend, field: str) -> None:
    led = ledger()
    await led.record(spend)
    with pytest.raises(BudgetExceeded) as err:
        await led.check()
    assert err.value.details["exceeded"] == [field]


async def test_check_passes_exactly_at_every_limit() -> None:
    led = ledger()
    await led.record(Spend(tokens=100, pages=10, steps=5, cost_usd=0.05))
    await led.check()


async def test_check_passes_on_an_empty_ledger() -> None:
    await ledger().check()


async def test_check_reports_every_exceeded_field() -> None:
    led = ledger()
    await led.record(Spend(tokens=101, steps=6))
    with pytest.raises(BudgetExceeded) as err:
        await led.check()
    assert err.value.details["exceeded"] == ["tokens", "steps"]


async def test_zero_limit_means_nothing_may_be_spent() -> None:
    zero = BudgetLimits(max_tokens=0, max_pages=0, max_steps=0, max_cost_usd=0)
    led = BudgetLedger(FakeRedis(), "r", zero)
    await led.check()
    await led.record(Spend(pages=1))
    with pytest.raises(BudgetExceeded):
        await led.check()


# ---- would_exceed(): pre-flight ------------------------------------------------------------
async def test_would_exceed_is_false_when_the_estimate_lands_exactly_on_the_limit() -> None:
    led = ledger()
    await led.record(Spend(steps=4, cost_usd=0.04))
    assert not await led.would_exceed(Spend(steps=1, cost_usd=0.01))


async def test_would_exceed_is_true_one_step_over() -> None:
    led = ledger()
    await led.record(Spend(steps=5))
    assert await led.would_exceed(Spend(steps=1))


async def test_would_exceed_with_a_zero_estimate_is_false_while_within_budget() -> None:
    assert not await ledger().would_exceed(Spend())


async def test_would_exceed_does_not_record_anything() -> None:
    led = ledger()
    await led.would_exceed(Spend(steps=1))
    assert await led.spent() == Spend()


async def test_exceeded_fields_names_which_limits_the_estimate_breaks() -> None:
    led = ledger()
    await led.record(Spend(steps=5, cost_usd=0.04))
    assert await led.exceeded_fields(Spend(steps=1, cost_usd=0.02)) == ["steps", "cost_usd"]
    assert await led.exceeded_fields(Spend(cost_usd=0.01)) == []


async def test_float_noise_in_cost_does_not_trip_the_limit() -> None:
    led = ledger()
    for _ in range(5):
        await led.record(Spend(cost_usd=0.01))  # 0.05 accumulates as 0.05000000000000001
    await led.check()


# ---- approvals raise the cost limit once ---------------------------------------------------
async def test_raise_cost_limit_lets_a_previously_blocked_estimate_through() -> None:
    led = ledger()
    await led.record(Spend(cost_usd=0.05))
    assert await led.would_exceed(Spend(cost_usd=0.02))
    await led.raise_cost_limit(0.02)
    assert not await led.would_exceed(Spend(cost_usd=0.02))
    assert (await led.limits()).max_cost_usd == pytest.approx(0.07)


async def test_raise_cost_limit_rejects_negative_amounts() -> None:
    with pytest.raises(ValueError):
        await ledger().raise_cost_limit(-0.01)


async def test_raised_limit_is_visible_to_a_second_ledger_instance() -> None:
    redis = FakeRedis()
    await ledger(redis).raise_cost_limit(0.5)
    assert (await ledger(redis).limits()).max_cost_usd == pytest.approx(0.55)


# ---- same contract against real Redis ------------------------------------------------------
@pytest.fixture
async def real(redis_url: str) -> AsyncIterator[BudgetLedger]:
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True)
    led = BudgetLedger(client, f"it_{uuid.uuid4().hex}", LIMITS)
    try:
        yield led
    finally:
        await client.delete(f"budget:{led.run_id}")
        await client.aclose()


@pytest.mark.integration
async def test_redis_accumulates_and_reads_back(real: BudgetLedger) -> None:
    await real.record(Spend(tokens=10, pages=1, steps=1, cost_usd=0.01))
    await real.record(Spend(tokens=5, steps=1, cost_usd=0.02))
    spent = await real.spent()
    assert (spent.tokens, spent.pages, spent.steps) == (15, 1, 2)
    assert spent.cost_usd == pytest.approx(0.03)


@pytest.mark.integration
async def test_redis_sets_a_seven_day_ttl(real: BudgetLedger) -> None:
    await real.record(Spend(steps=1))
    assert 7 * 24 * 3600 - 60 < await real.ttl_s() <= 7 * 24 * 3600


@pytest.mark.integration
async def test_redis_concurrent_increments_are_atomic(real: BudgetLedger) -> None:
    await asyncio.gather(*(real.record(Spend(steps=1, cost_usd=0.001)) for _ in range(100)))
    spent = await real.spent()
    assert spent.steps == 100
    assert spent.cost_usd == pytest.approx(0.1)


@pytest.mark.integration
async def test_redis_check_and_would_exceed(real: BudgetLedger) -> None:
    await real.record(Spend(steps=5))
    await real.check()
    assert await real.would_exceed(Spend(steps=1))
    await real.record(Spend(steps=1))
    with pytest.raises(BudgetExceeded):
        await real.check()


@pytest.mark.integration
async def test_redis_raise_cost_limit(real: BudgetLedger) -> None:
    await real.record(Spend(cost_usd=0.05))
    await real.raise_cost_limit(0.03)
    await real.check()
    assert not await real.would_exceed(Spend(cost_usd=0.03))
