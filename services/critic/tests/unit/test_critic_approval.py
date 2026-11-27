from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.config import ConfigError
from svc_critic.models.approval import ApprovalRequest
from svc_critic.services.approval import (
    RunBudget,
    decide,
    evaluate,
    load_budgets,
    parse_budgets,
)

REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"
D = Decimal
VALIDATE = RunBudget(max_cost_usd=D("0.05"), approval_threshold_usd=D("0.05"))


def test_real_budgets_file_loads_every_run_kind() -> None:
    budgets = load_budgets(REPO_CONFIG)
    assert set(budgets) == {"validate", "discover", "whatif", "challenge", "eval"}
    assert budgets["validate"] == VALIDATE
    assert budgets["discover"].max_cost_usd == D("0.25")
    assert budgets["eval"].approval_threshold_usd == D("1.0")


@pytest.mark.parametrize(
    ("spent", "estimate", "decision"),
    [
        ("0", "0", "proceed"),  # zero
        ("0", "0.01", "proceed"),
        (
            "0.03",
            "0.02",
            "proceed",
        ),  # exactly at threshold (float 0.03+0.02 is fine, 0.01+0.04 too)
        ("0.04", "0.01", "proceed"),
        ("0.01", "0.02", "proceed"),
        ("0", "0.05", "proceed"),  # estimate alone exactly at threshold
        ("0", "0.0500001", "needs_approval"),  # just over
        ("0.05", "0.0000001", "needs_approval"),
        ("0.04", "0.0100001", "needs_approval"),
        ("0.05", "0.05", "needs_approval"),  # projected exactly at hard ceiling (2 x max)
        ("0.05", "0.0500001", "blocked"),  # just over the ceiling
        ("0", "5", "blocked"),
        ("0.2", "0.01", "blocked"),
    ],
)
def test_validate_run_boundaries(spent: str, estimate: str, decision: str) -> None:
    assert decide(VALIDATE, D(spent), D(estimate)).decision == decision


def test_a_free_step_always_proceeds_even_when_over_budget() -> None:
    # the deterministic verdict step costs nothing and must never be blocked
    result = decide(VALIDATE, D("9"), D("0"))
    assert result.decision == "proceed"
    assert result.needs_approval is False


def test_decision_carries_the_numbers() -> None:
    result = decide(VALIDATE, D("0.04"), D("0.02"))
    assert result.decision == "needs_approval"
    assert result.needs_approval is True
    assert result.spent == 0.04
    assert result.budget == 0.05
    assert result.approval_threshold == 0.05
    assert result.projected == 0.06
    assert "0.06" in result.reason


def test_threshold_above_budget_uses_the_threshold_as_ceiling() -> None:
    eval_budget = RunBudget(max_cost_usd=D("0.05"), approval_threshold_usd=D("1.0"))
    assert decide(eval_budget, D("0"), D("1.0")).decision == "proceed"
    assert decide(eval_budget, D("0"), D("1.01")).decision == "blocked"


def test_threshold_below_budget_has_a_proceed_band_then_approval() -> None:
    budget = RunBudget(max_cost_usd=D("1.00"), approval_threshold_usd=D("0.50"))
    assert decide(budget, D("0"), D("0.50")).decision == "proceed"
    assert decide(budget, D("0"), D("0.51")).decision == "needs_approval"
    assert decide(budget, D("0"), D("2.00")).decision == "needs_approval"
    assert decide(budget, D("0"), D("2.01")).decision == "blocked"


def test_custom_ceiling_multiplier() -> None:
    assert decide(VALIDATE, D("0"), D("0.06"), hard_cap_multiplier=D("1")).decision == "blocked"
    assert (
        decide(VALIDATE, D("0"), D("0.14"), hard_cap_multiplier=D("3")).decision == "needs_approval"
    )


def test_evaluate_uses_the_run_kind_and_float_inputs_without_rounding_errors() -> None:
    budgets = {"validate": VALIDATE}
    # 0.01 + 0.02 style float sums must not tip over the threshold
    req = ApprovalRequest(
        run_id="r", kind="validate", action="scout", estimated_cost_usd=0.02, spent_usd=0.03
    )
    assert evaluate(budgets, req).decision == "proceed"
    req2 = ApprovalRequest(
        run_id="r", kind="validate", action="scout", estimated_cost_usd=0.03, spent_usd=0.03
    )
    assert evaluate(budgets, req2).decision == "needs_approval"


def test_evaluate_unknown_kind_in_config_is_a_config_error() -> None:
    req = ApprovalRequest(
        run_id="r", kind="discover", action="a", estimated_cost_usd=0.0, spent_usd=0.0
    )
    with pytest.raises(ConfigError):
        evaluate({"validate": VALIDATE}, req)


@pytest.mark.parametrize(
    "overrides",
    [
        {"estimated_cost_usd": -0.0001},
        {"spent_usd": -1},
        {"estimated_cost_usd": float("nan")},
        {"estimated_cost_usd": float("inf")},
        {"spent_usd": float("inf")},
        {"kind": "bogus"},
        {"action": ""},
        {"action": "x" * 201},
        {"run_id": ""},
        {"unknown_field": 1},
    ],
)
def test_request_validation_rejects_bad_input(overrides: dict[str, object]) -> None:
    base: dict[str, object] = {
        "run_id": "r",
        "kind": "validate",
        "action": "scout",
        "estimated_cost_usd": 0.01,
        "spent_usd": 0.0,
    }
    with pytest.raises(ValidationError):
        ApprovalRequest(**{**base, **overrides})


def test_request_accepts_zero_and_requires_spent() -> None:
    ApprovalRequest(run_id="r", kind="eval", action="a", estimated_cost_usd=0, spent_usd=0)
    with pytest.raises(ValidationError):
        ApprovalRequest(run_id="r", kind="eval", action="a", estimated_cost_usd=0)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "data",
    [
        {"validate": {"max_cost_usd": "abc", "approval_threshold_usd": 0.05}},
        {"validate": {"max_cost_usd": -1, "approval_threshold_usd": 0.05}},
        {"validate": {"max_cost_usd": 0.05}},
        {"validate": "nope"},
        {"validate": {"max_cost_usd": True, "approval_threshold_usd": 0.05}},
    ],
)
def test_malformed_budget_config_is_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_budgets(data)


def test_parse_ignores_non_run_sections() -> None:
    parsed = parse_budgets(
        {
            "version": "b1",
            "defaults": {"max_retries": 2},
            "concurrency": {"scorers": 4},
            "approval_timeout_s": 600,
            "validate": {"max_cost_usd": 0.05, "approval_threshold_usd": 0.05},
        }
    )
    assert set(parsed) == {"validate"}


def test_missing_budget_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_budgets(tmp_path)
