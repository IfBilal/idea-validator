from pathlib import Path

import pytest
import yaml

from common.config import ConfigError
from svc_orchestrator.services.run_config import load_run_config, run_config_from

REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"

MINIMAL = {
    "validate": {
        "max_retries": 2,
        "timeouts_s": {"tool_fetch": 10, "kb_search": 5, "scorer": 60, "llm": 60, "verdict": 2},
        "max_tokens": 1000,
        "max_pages": 4,
        "max_steps": 20,
        "max_cost_usd": 0.05,
        "approval_threshold_usd": 0.05,
        "deep_search": {"max_pages": 20, "est_cost_per_page_usd": 0.0008},
    },
    "concurrency": {"scorers": 4},
    "approval_timeout_s": 600,
}


def test_repo_budgets_yaml_loads_for_validate() -> None:
    cfg = load_run_config("validate", config_dir=REPO_CONFIG)
    assert cfg.max_retries == 2
    assert cfg.timeouts.scorer == 60
    assert cfg.timeouts.verdict == 2
    assert cfg.limits.max_steps == 80
    assert cfg.limits.approval_threshold_usd == 0.05
    assert cfg.deep_search_pages == 20
    assert cfg.deep_search_cost_usd == pytest.approx(0.016)
    assert cfg.scorer_concurrency == 4
    assert cfg.approval_timeout_s == 600


def test_unlisted_timeouts_fall_back_to_the_workflow_table() -> None:
    cfg = run_config_from(MINIMAL, "validate")
    assert cfg.timeouts.features == 10  # doc 07 §2
    assert cfg.timeouts.cache == 1


def test_missing_kind_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        run_config_from(MINIMAL, "discover")


@pytest.mark.parametrize("retries", [-1, 3])
def test_retries_are_bounded_to_zero_through_two(retries: int) -> None:
    bad = {**MINIMAL, "validate": {**MINIMAL["validate"], "max_retries": retries}}  # type: ignore[dict-item]
    with pytest.raises(ConfigError, match="max_retries"):
        run_config_from(bad, "validate")


def test_zero_retries_is_allowed() -> None:
    ok = {**MINIMAL, "validate": {**MINIMAL["validate"], "max_retries": 0}}  # type: ignore[dict-item]
    assert run_config_from(ok, "validate").max_retries == 0


def test_non_positive_timeout_is_a_config_error() -> None:
    bad_section = {**MINIMAL["validate"], "timeouts_s": {"scorer": 0}}  # type: ignore[dict-item]
    with pytest.raises(ConfigError, match="timeout"):
        run_config_from({**MINIMAL, "validate": bad_section}, "validate")


def test_missing_deep_search_defaults_to_no_scouting() -> None:
    section = {k: v for k, v in MINIMAL["validate"].items() if k != "deep_search"}  # type: ignore[union-attr]
    cfg = run_config_from({**MINIMAL, "validate": section}, "validate")
    assert cfg.deep_search_pages == 0
    assert cfg.deep_search_cost_usd == 0


def test_load_from_a_directory(tmp_path: Path) -> None:
    (tmp_path / "budgets.yaml").write_text(yaml.safe_dump(MINIMAL))
    assert load_run_config("validate", config_dir=tmp_path).limits.max_pages == 4
