from collections.abc import Callable

from common.schemas.types import DIMENSIONS
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import run_core
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]
ALL = {d: 5.0 for d in DIMENSIONS}


def test_score_exactly_t_go_is_go(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build("portfolio", {d: 3.5 for d in DIMENSIONS}), cfg)
    assert (core.score, core.verdict) == (70.0, "GO")


def test_score_just_below_t_go_is_pivot(build: Build, cfg: EngineConfig) -> None:
    scores = {**{d: 3.5 for d in DIMENSIONS}, "feasibility": 3.498}
    core = run_core(build("portfolio", scores), cfg)
    assert core.score == 69.99
    assert core.verdict == "PIVOT"


def test_score_exactly_t_no_is_pivot(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build("portfolio", {d: 2.5 for d in DIMENSIONS}), cfg)
    assert (core.score, core.verdict) == (50.0, "PIVOT")


def test_score_just_below_t_no_is_no_go(build: Build, cfg: EngineConfig) -> None:
    scores = {**{d: 2.5 for d in DIMENSIONS}, "feasibility": 2.498}
    core = run_core(build("portfolio", scores), cfg)
    assert core.score == 49.99
    assert core.verdict == "NO-GO"


def test_floor_value_exactly_1_5_is_not_a_breach(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(scores={**ALL, "competition": 1.5}), cfg)
    assert core.verdict == "GO"


def test_dimension_below_floor_blocks_go_even_with_a_high_score(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(scores={**ALL, "competition": 1.49}), cfg)
    assert core.score is not None and core.score > 70
    assert core.verdict == "PIVOT"
    assert core.weakest == "competition"
    assert "floor breach" in core.reasons[-1]


def test_learning_below_floor_is_ignored(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build("course_project", {**ALL, "learning": 0.5}), cfg)
    assert core.verdict == "GO"


def test_floor_breach_with_a_low_score_is_no_go(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(scores={d: 1.2 for d in DIMENSIONS}), cfg)
    assert core.verdict == "NO-GO"


def test_weakest_dimension_tie_goes_to_the_larger_weight(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(scores={d: 1.0 for d in DIMENSIONS}), cfg)
    assert core.weakest == "demand_pain"  # startup weight 25 is the largest


def test_weakest_dimension_tie_with_equal_weights_uses_fixed_order(
    build: Build, cfg: EngineConfig
) -> None:
    scores = {**ALL, "demand_pain": 3.0, "competition": 1.0, "differentiation": 1.0}
    core = run_core(build(scores=scores), cfg)
    assert core.weakest == "competition"  # startup weights 20 and 20: competition comes first


def test_weakest_dimension_without_a_breach_is_the_lowest_of_all_six(
    build: Build, cfg: EngineConfig
) -> None:
    scores = {**{d: 3.0 for d in DIMENSIONS}, "learning": 2.0}
    core = run_core(build("course_project", scores), cfg)
    assert core.verdict == "PIVOT"
    assert core.weakest == "learning"
