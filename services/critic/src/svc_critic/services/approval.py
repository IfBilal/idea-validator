"""Approval evaluation (U4): may a step run, does it need the user's consent, or is it blocked?

Pure decision logic over ``config/budgets.yaml``. Money is compared as ``Decimal`` so that
``0.03 + 0.02`` is exactly the ``0.05`` threshold, never ``0.0500000001``.

Rules (``projected = spent + estimated``):
  * a free step (estimate 0) always proceeds, so the deterministic verdict step can never be blocked
  * ``projected <= approval_threshold_usd``                -> proceed
  * ``projected <= ceiling``                               -> needs_approval
  * otherwise                                              -> blocked
where ``ceiling = max(max_cost_usd * hard_cap_multiplier, approval_threshold_usd)``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from common.config import ConfigError, load_yaml_config

from ..models.approval import RUN_KINDS, ApprovalDecision, ApprovalRequest

DEFAULT_HARD_CAP_MULTIPLIER = Decimal("2")


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_cost_usd: Decimal
    approval_threshold_usd: Decimal


def _money(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ConfigError(f"{where} must be a number")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigError(f"{where} must be a number") from exc
    if not amount.is_finite() or amount < 0:
        raise ConfigError(f"{where} must be a non-negative number")
    return amount


def parse_budgets(data: Mapping[str, Any]) -> dict[str, RunBudget]:
    budgets: dict[str, RunBudget] = {}
    for kind in RUN_KINDS:
        if kind not in data:
            continue
        section = data[kind]
        if not isinstance(section, Mapping):
            raise ConfigError(f"budgets.{kind} must be a mapping")
        try:
            budgets[kind] = RunBudget(
                max_cost_usd=_money(section["max_cost_usd"], f"budgets.{kind}.max_cost_usd"),
                approval_threshold_usd=_money(
                    section["approval_threshold_usd"], f"budgets.{kind}.approval_threshold_usd"
                ),
            )
        except KeyError as exc:
            raise ConfigError(f"budgets.{kind} is missing {exc.args[0]}") from exc
    return budgets


def load_budgets(config_dir: Path | None = None) -> dict[str, RunBudget]:
    return parse_budgets(load_yaml_config("budgets", config_dir=config_dir))


def decide(
    budget: RunBudget,
    spent: Decimal,
    estimated: Decimal,
    *,
    hard_cap_multiplier: Decimal = DEFAULT_HARD_CAP_MULTIPLIER,
) -> ApprovalDecision:
    projected = spent + estimated
    threshold = budget.approval_threshold_usd
    ceiling = max(budget.max_cost_usd * hard_cap_multiplier, threshold)
    if estimated == 0 or projected <= threshold:
        decision, reason = "proceed", f"projected cost {projected} within threshold {threshold}"
    elif projected <= ceiling:
        decision = "needs_approval"
        reason = f"projected cost {projected} exceeds approval threshold {threshold}"
    else:
        decision = "blocked"
        reason = f"projected cost {projected} exceeds hard cap {ceiling}"
    return ApprovalDecision(
        decision=decision,
        needs_approval=decision == "needs_approval",
        spent=float(spent),
        budget=float(budget.max_cost_usd),
        approval_threshold=float(threshold),
        projected=float(projected),
        reason=reason,
    )


def evaluate(
    budgets: Mapping[str, RunBudget],
    request: ApprovalRequest,
    *,
    hard_cap_multiplier: Decimal = DEFAULT_HARD_CAP_MULTIPLIER,
) -> ApprovalDecision:
    budget = budgets.get(request.kind)
    if budget is None:
        raise ConfigError(f"no budget configured for run kind {request.kind!r}")
    return decide(
        budget,
        Decimal(str(request.spent_usd)),
        Decimal(str(request.estimated_cost_usd)),
        hard_cap_multiplier=hard_cap_multiplier,
    )


class BudgetBook:
    """Lazily loaded, cached budgets plus the ceiling multiplier (settings)."""

    def __init__(self, config_dir: Path, multiplier: Decimal = DEFAULT_HARD_CAP_MULTIPLIER) -> None:
        self._config_dir = config_dir
        self._budgets: dict[str, RunBudget] | None = None
        self.multiplier = multiplier

    def get(self) -> dict[str, RunBudget]:
        if self._budgets is None:
            self._budgets = load_budgets(self._config_dir)
        return self._budgets
