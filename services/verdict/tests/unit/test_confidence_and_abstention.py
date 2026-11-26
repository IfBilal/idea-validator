from collections.abc import Callable

from conftest import GOOD

from common.schemas.types import DIMENSIONS
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import evaluate, run_core
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]


def test_confidence_follows_the_documented_formula(build: Build, cfg: EngineConfig) -> None:
    # 0.7 * mean(0.5) + 0.3 * min(1, 5/5) = 0.65
    core = run_core(build(conf={d: 0.5 for d in DIMENSIONS}, n_sources=5), cfg)
    assert core.confidence == 0.65
    assert core.verdict == "GO"


def test_sources_beyond_five_do_not_raise_confidence(build: Build, cfg: EngineConfig) -> None:
    five = run_core(build(n_sources=5), cfg).confidence
    ten = run_core(build(n_sources=10), cfg).confidence
    assert five == ten


def test_confidence_exactly_at_the_minimum_passes(build: Build, cfg: EngineConfig) -> None:
    only_dims = cfg.model_copy(
        update={"confidence": cfg.confidence.model_copy(update={"w_dims": 1.0, "w_sources": 0.0})}
    )
    assert run_core(build(conf={d: 0.5 for d in DIMENSIONS}), only_dims).verdict == "GO"


def test_confidence_just_below_the_minimum_is_insufficient_evidence(
    build: Build, cfg: EngineConfig
) -> None:
    only_dims = cfg.model_copy(
        update={"confidence": cfg.confidence.model_copy(update={"w_dims": 1.0, "w_sources": 0.0})}
    )
    core = run_core(build(conf={d: 0.499 for d in DIMENSIONS}), only_dims)
    assert core.verdict == "INSUFFICIENT_EVIDENCE"
    assert "confidence" in core.reasons[-1]


def test_low_confidence_names_the_weak_dimensions(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(conf={d: 0.2 for d in DIMENSIONS}), cfg)
    assert core.verdict == "INSUFFICIENT_EVIDENCE"
    assert "demand_pain" in core.reasons[-1]
    assert core.score is not None  # the score is still reported


def test_any_abstention_is_insufficient_evidence_naming_the_dimensions(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(scores={**GOOD, "risk": None, "learning": None}), cfg)
    assert core.verdict == "INSUFFICIENT_EVIDENCE"
    assert core.reasons[-1] == "abstained (no evidence): risk, learning"
    assert core.score is None


def test_abstention_is_reported_with_zero_sensitivity_runs(build: Build, cfg: EngineConfig) -> None:
    result = evaluate(build(scores={**GOOD, "risk": None}), cfg)
    assert result.sensitivity.runs == 0
