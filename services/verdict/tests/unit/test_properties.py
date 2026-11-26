from collections.abc import Callable

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from common.schemas.types import DIMENSIONS
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import run_core
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]
score = st.floats(min_value=0.0, max_value=5.0, allow_nan=False)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],  # `build` is a stateless factory
)
@given(
    base=st.lists(score, min_size=6, max_size=6),
    dim=st.sampled_from(DIMENSIONS),
    bump=st.floats(min_value=0.0, max_value=5.0, allow_nan=False),
    goal=st.sampled_from(["startup", "portfolio", "course_project"]),
)
def test_weighted_score_never_decreases_when_any_score_increases(
    build: Build, cfg: EngineConfig, base: list[float], dim: str, bump: float, goal: str
) -> None:
    low = dict(zip(DIMENSIONS, base, strict=True))
    high = {**low, dim: min(5.0, low[dim] + bump)}
    conf = {d: 0.9 for d in DIMENSIONS}
    first = run_core(build(goal, low, conf), cfg).score
    second = run_core(build(goal, high, conf), cfg).score
    assert first is not None and second is not None
    assert second >= first
