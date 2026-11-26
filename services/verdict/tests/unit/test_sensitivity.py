from collections.abc import Callable

import pytest
from conftest import WORKED_EXAMPLE, WORKED_EXAMPLE_CONF
from hypothesis import given
from hypothesis import strategies as st

from common.schemas.types import DIMENSIONS
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import DIMS, evaluate, shift_weights
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]


def test_exactly_fourteen_runs_for_a_scored_verdict(build: Build, cfg: EngineConfig) -> None:
    assert evaluate(build(), cfg).sensitivity.runs == 14


def test_stable_verdict_has_no_flips(build: Build, cfg: EngineConfig) -> None:
    result = evaluate(build(), cfg)
    assert result.verdict == "GO"
    assert result.stability == "STABLE"
    assert result.sensitivity.flipped == 0


def test_worked_example_is_a_borderline_go_with_four_flips(build: Build, cfg: EngineConfig) -> None:
    result = evaluate(
        build("portfolio", WORKED_EXAMPLE, WORKED_EXAMPLE_CONF, effort=100, hpw=10, weeks=12), cfg
    )
    assert (result.verdict, result.stability, result.score) == ("GO", "BORDERLINE", 70.5)
    assert result.sensitivity.flipped == 4
    flips = {f.change: (f.verdict, f.score) for f in result.sensitivity.flips}
    assert flips["weight feasibility -20%"] == ("PIVOT", 69.8667)
    assert flips["weight competition +20%"] == ("PIVOT", 69.4235)
    assert flips["score competition -1"] == ("PIVOT", 67.5)


@given(
    raw=st.lists(st.floats(min_value=1.0, max_value=50.0), min_size=6, max_size=6),
    dim=st.sampled_from(DIMENSIONS),
    factor=st.sampled_from([-0.2, 0.2]),
)
def test_shifting_a_weight_keeps_the_total_at_100_and_shares_proportionally(
    raw: list[float], dim: str, factor: float
) -> None:
    total = sum(raw)
    weights = {d: 100 * w / total for d, w in zip(DIMS, raw, strict=True)}
    shifted = shift_weights(weights, dim, factor)
    assert sum(shifted.values()) == pytest.approx(100.0)
    assert shifted[dim] == pytest.approx(weights[dim] * (1 + factor))
    others = [d for d in DIMS if d != dim]
    ratios = [shifted[d] / weights[d] for d in others]
    assert max(ratios) - min(ratios) < 1e-9
