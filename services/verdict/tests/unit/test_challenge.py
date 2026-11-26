from collections.abc import Callable

from conftest import GOOD

from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import run_core
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]


def objections(n: int, dimension: str = "competition") -> list[dict[str, str]]:
    return [{"dimension": dimension, "objection_id": f"o{i}"} for i in range(n)]


def test_one_accepted_objection_lowers_score_by_half_and_confidence_by_a_tenth(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(scores={**GOOD, "competition": 3.0}, objections=objections(1)), cfg)
    assert core.adjusted_scores["competition"] == 2.5
    assert core.adjusted_confidences["competition"] == 0.6
    assert core.adjusted_scores["demand_pain"] == 4.0


def test_three_objections_are_capped_at_a_one_point_score_drop(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(scores={**GOOD, "competition": 3.0}, objections=objections(3)), cfg)
    assert core.adjusted_scores["competition"] == 2.0
    assert core.adjusted_confidences["competition"] < 0.7 - 0.25  # confidence is not capped


def test_adjustments_never_go_below_zero(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(scores={**GOOD, "competition": 0.3}, objections=objections(2)), cfg)
    assert core.adjusted_scores["competition"] == 0.0


def test_an_abstained_dimension_stays_abstained(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(scores={**GOOD, "risk": None}, objections=objections(1, "risk")), cfg)
    assert core.adjusted_scores["risk"] is None
