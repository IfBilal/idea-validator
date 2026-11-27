"""Per-run-kind configuration read from ``config/budgets.yaml``: budgets, timeouts, retries."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from common.budget import BudgetLimits, limits_from_config
from common.config import ConfigError, load_yaml_config

MAX_RETRIES_CAP = 2  # CLAUDE.md rule 9
DEBATE_ROUNDS_CAP = 3  # hard cap: the debate never exceeds 3 rounds
MAX_CHILDREN = 10


class Timeouts(BaseModel):
    """Seconds per outbound call. Defaults are the workflow table in docs/harness/07 §2."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    cache: float = 1
    profile: float = 5
    kb_search: float = 5
    tool_fetch: float = 10
    features: float = 10
    scorer: float = 60
    scout: float = 60
    verdict: float = 2
    critic: float = 5
    approval: float = 2
    llm: float = 60


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    max_retries: int
    timeouts: Timeouts
    limits: BudgetLimits
    deep_search_pages: int
    deep_search_cost_usd: float
    scorer_concurrency: int
    approval_timeout_s: float
    debate_max_rounds: int = 3
    children_concurrency: int = 3


def _int_between(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def run_config_from(config: Mapping[str, Any], kind: str) -> RunConfig:
    limits = limits_from_config(config, kind)
    section: Mapping[str, Any] = config[kind]
    retries = int(section.get("max_retries", MAX_RETRIES_CAP))
    if not 0 <= retries <= MAX_RETRIES_CAP:
        raise ConfigError(f"max_retries must be between 0 and {MAX_RETRIES_CAP}, got {retries}")
    try:
        timeouts = Timeouts.model_validate(section.get("timeouts_s", {}))
    except ValidationError as exc:
        raise ConfigError(f"invalid timeouts for {kind!r}: {exc}") from exc
    if any(value <= 0 for value in timeouts.model_dump().values()):
        raise ConfigError(f"every timeout for {kind!r} must be positive")
    rounds = (section.get("debate") or {}).get("max_rounds", DEBATE_ROUNDS_CAP)
    if not _int_between(rounds, 1, DEBATE_ROUNDS_CAP):
        raise ConfigError(
            f"debate max_rounds must be an integer 1..{DEBATE_ROUNDS_CAP}: {rounds!r}"
        )
    children = section.get("validate_children_concurrency", 3)
    if not _int_between(children, 1, MAX_CHILDREN):
        raise ConfigError(f"validate_children_concurrency must be an integer 1..{MAX_CHILDREN}")
    deep = section.get("deep_search") or {}
    pages = int(deep.get("max_pages", 0))
    per_page = float(deep.get("est_cost_per_page_usd", 0.0))
    return RunConfig(
        kind=kind,
        max_retries=retries,
        timeouts=timeouts,
        limits=limits,
        deep_search_pages=pages,
        deep_search_cost_usd=pages * per_page,
        scorer_concurrency=int((config.get("concurrency") or {}).get("scorers", 4)),
        approval_timeout_s=float(config.get("approval_timeout_s", 600)),
        debate_max_rounds=rounds,
        children_concurrency=children,
    )


def load_run_config(kind: str, *, config_dir: Path | None = None) -> RunConfig:
    return run_config_from(load_yaml_config("budgets", config_dir=config_dir), kind)
