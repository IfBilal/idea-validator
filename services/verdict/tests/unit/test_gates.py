from collections.abc import Callable

from conftest import GOOD

from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import evaluate, run_core
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]


def test_evidence_gate_fails_on_too_few_sources(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(n_sources=2), cfg)
    assert core.verdict == "INSUFFICIENT_EVIDENCE"
    assert core.gates["evidence"] == "fail"
    assert "2 of 3 independent sources" in core.reasons[-1]


def test_evidence_gate_fails_on_too_few_platforms(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(n_sources=3, n_platforms=1), cfg)
    assert core.verdict == "INSUFFICIENT_EVIDENCE"
    assert "1 of 2 platforms" in core.reasons[-1]


def test_evidence_gate_edge_three_sources_two_platforms_passes(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(n_sources=3, n_platforms=2), cfg)
    assert core.gates["evidence"] == "pass"
    assert core.verdict == "GO"


def test_feasibility_gate_fails_and_names_the_gap(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(effort=30, hpw=20, weeks=1, team=1), cfg)
    assert core.verdict == "NO-GO"
    assert core.gates["feasibility"] == "fail"
    assert core.reasons[-1] == "feasibility gate: needs ~30 h, available 20 h (gap 10 h)"


def test_feasibility_gate_edge_effort_equal_to_available_passes(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(effort=240, hpw=10, weeks=12, team=2), cfg)
    assert core.gates["feasibility"] == "pass"


def test_risk_gate_fails_and_names_the_blocker(build: Build, cfg: EngineConfig) -> None:
    core = run_core(build(risk="collects health data of minors"), cfg)
    assert core.verdict == "NO-GO"
    assert core.gates["risk"] == "fail"
    assert "collects health data of minors" in core.reasons[-1]


def test_duplicate_gate_fails_and_names_the_product(build: Build, cfg: EngineConfig) -> None:
    core = run_core(
        build(scores={**GOOD, "differentiation": 2.5}, dup="card_notion01 (Notion)"), cfg
    )
    assert core.verdict == "NO-GO"
    assert core.gates["duplicate"] == "fail"
    assert "card_notion01 (Notion)" in core.reasons[-1]


def test_duplicate_gate_is_overridden_at_differentiation_three(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(
        build(scores={**GOOD, "differentiation": 3.0}, dup="card_notion01 (Notion)"), cfg
    )
    assert core.gates["duplicate"] == "pass"


def test_duplicate_gate_fails_when_differentiation_abstained(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(scores={**GOOD, "differentiation": None}, dup="card_x (X)"), cfg)
    assert core.verdict == "NO-GO"
    assert core.gates["duplicate"] == "fail"


def test_first_failing_gate_wins_and_later_gates_are_not_evaluated(
    build: Build, cfg: EngineConfig
) -> None:
    core = run_core(build(n_sources=1, risk="blocker", dup="card_x (X)"), cfg)
    assert core.verdict == "INSUFFICIENT_EVIDENCE"
    assert core.gates == {
        "evidence": "fail",
        "feasibility": "not_evaluated",
        "risk": "not_evaluated",
        "duplicate": "not_evaluated",
    }


def test_a_gate_decided_verdict_has_no_sensitivity_runs(build: Build, cfg: EngineConfig) -> None:
    result = evaluate(build(risk="blocker"), cfg)
    assert result.sensitivity.runs == 0
    assert result.stability == "STABLE"
    assert result.score is None
