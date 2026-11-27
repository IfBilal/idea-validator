from pathlib import Path
from typing import Any

import pytest

from common.config import ConfigError
from svc_orchestrator.services.run_config import load_run_config, run_config_from

REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"

DISCOVER: dict[str, Any] = {
    "max_retries": 2,
    "max_tokens": 1000,
    "max_pages": 4,
    "max_steps": 20,
    "max_cost_usd": 0.25,
    "approval_threshold_usd": 0.25,
    "debate": {"max_rounds": 3},
    "validate_children_concurrency": 3,
}


def cfg(**over: Any) -> dict[str, Any]:
    return {"discover": {**DISCOVER, **over}}


def test_the_repo_discover_config_has_the_debate_cap_and_concurrency() -> None:
    loaded = load_run_config("discover", config_dir=REPO_CONFIG)
    assert loaded.debate_max_rounds == 3
    assert loaded.children_concurrency == 3
    assert loaded.limits.max_steps == 400


@pytest.mark.parametrize("rounds", [1, 2, 3])
def test_debate_rounds_within_the_cap_are_accepted(rounds: int) -> None:
    got = run_config_from(cfg(debate={"max_rounds": rounds}), "discover")
    assert got.debate_max_rounds == rounds


@pytest.mark.parametrize("rounds", [0, -1, 4, 10, True, "3"])
def test_debate_rounds_cannot_exceed_three_or_be_non_positive(rounds: Any) -> None:
    with pytest.raises(ConfigError, match="max_rounds"):
        run_config_from(cfg(debate={"max_rounds": rounds}), "discover")


@pytest.mark.parametrize("n", [0, -1, 11, True])
def test_children_concurrency_is_bounded(n: Any) -> None:
    with pytest.raises(ConfigError, match="concurrency"):
        run_config_from(cfg(validate_children_concurrency=n), "discover")


@pytest.mark.parametrize("n", [1, 3, 10])
def test_children_concurrency_boundaries_are_accepted(n: int) -> None:
    got = run_config_from(cfg(validate_children_concurrency=n), "discover")
    assert got.children_concurrency == n


def test_other_kinds_default_to_three_rounds_and_three_children() -> None:
    plain = {
        k: v for k, v in DISCOVER.items() if k not in ("debate", "validate_children_concurrency")
    }
    got = run_config_from({"validate": plain}, "validate")
    assert (got.debate_max_rounds, got.children_concurrency) == (3, 3)
