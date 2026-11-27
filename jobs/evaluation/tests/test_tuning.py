import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from evaluation.tuning import (
    BOUNDS,
    FALSE_GO_COST,
    TuningCase,
    bump_version,
    case_cost,
    cross_validate,
    evaluate_cases,
    kfold,
    load_cases,
    propose,
    threshold_grid,
    tune_thresholds,
    tune_weights,
    with_params,
)
from svc_verdict.services.engine_config import EngineConfig, parse_engine_config

from common.schemas.types import DIMENSIONS

CONFIG = Path(__file__).resolve().parents[3] / "config" / "verdict.yaml"


@pytest.fixture(scope="module")
def cfg() -> EngineConfig:
    return parse_engine_config(yaml.safe_load(CONFIG.read_text()))


def case(
    cid: str, human: str, goal: str = "portfolio", level: float = 3.5, **scores: float
) -> TuningCase:
    values = {d: level for d in DIMENSIONS} | scores
    payload: dict[str, Any] = {
        "profile": {"goal": goal, "hours_per_week": 10, "weeks_available": 12, "team_size": 2},
        "scores": [
            {"dimension": d, "score": values[d], "confidence": 0.8, "evidence_ids": []}
            for d in DIMENSIONS
        ],
        "gate_inputs": {"n_sources": 5, "n_platforms": 2, "estimated_effort_hours": 100},
    }
    return TuningCase.model_validate({"id": cid, "input": payload, "human_verdict": human})


def dataset(n_per: int = 8) -> list[TuningCase]:
    """Clear GOs (4.2), clear NO-GOs (1.8) and mid items humans call GO at 3.3 (default says PIVOT)."""
    cases: list[TuningCase] = []
    for i in range(n_per):
        cases.append(case(f"go{i}", "GO", level=4.2))
        cases.append(case(f"no{i}", "NO-GO", level=1.8))
        cases.append(case(f"mid{i}", "GO", level=3.3))  # 66 points: default T_go=70 says PIVOT
    return cases


# ---- case cost
@pytest.mark.parametrize(
    ("human", "system", "cost"),
    [
        ("GO", "GO", 0), ("PIVOT", "PIVOT", 0), ("NO-GO", "NO-GO", 0),
        ("NO-GO", "GO", FALSE_GO_COST), ("PIVOT", "GO", 2), ("GO", "NO-GO", 2),
        ("GO", "PIVOT", 1), ("PIVOT", "NO-GO", 1), ("NO-GO", "PIVOT", 1),
        ("GO", "INSUFFICIENT_EVIDENCE", 1), ("INSUFFICIENT_EVIDENCE", "GO", 1),
    ],
)  # fmt: skip
def test_case_cost_table(human: str, system: str, cost: float) -> None:
    assert case_cost(human, system) == cost


def test_a_false_go_is_the_most_expensive_error() -> None:
    labels = ["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"]
    worst = max(case_cost(h, s) for h in labels for s in labels)
    assert worst == case_cost("NO-GO", "GO")


# ---- engine evaluation with a parameter set
def test_evaluate_cases_uses_the_real_engine(cfg: EngineConfig) -> None:
    cases = [case("a", "GO", level=4.2), case("b", "NO-GO", level=1.8), case("c", "GO", level=3.3)]
    assert evaluate_cases(cases, cfg) == ["GO", "NO-GO", "PIVOT"]


def test_with_params_changes_only_what_was_asked(cfg: EngineConfig) -> None:
    changed = with_params(cfg, t_go=65, t_no=45, floor=1.25)
    assert (changed.thresholds.T_go, changed.thresholds.T_no, changed.thresholds.floor) == (
        65,
        45,
        1.25,
    )
    assert changed.weights == cfg.weights
    assert changed.engine_version == cfg.engine_version


def test_with_params_validates(cfg: EngineConfig) -> None:
    with pytest.raises(ValueError, match="T_no <= T_go"):
        with_params(cfg, t_go=50, t_no=60, floor=1.5)


# ---- the grid
def test_the_grid_contains_the_current_values_and_respects_bounds_and_spacing(
    cfg: EngineConfig,
) -> None:
    grid = threshold_grid(cfg)
    assert (70.0, 50.0, 1.5) in grid
    for t_go, t_no, floor in grid:
        assert BOUNDS["T_go"][0] <= t_go <= BOUNDS["T_go"][1]
        assert BOUNDS["T_no"][0] <= t_no <= BOUNDS["T_no"][1]
        assert BOUNDS["floor"][0] <= floor <= BOUNDS["floor"][1]
        assert t_go - t_no >= 5


def test_the_grid_is_deterministic_and_unique(cfg: EngineConfig) -> None:
    assert threshold_grid(cfg) == threshold_grid(cfg)
    assert len(threshold_grid(cfg)) == len(set(threshold_grid(cfg)))


# ---- threshold tuning
def test_tuning_lowers_t_go_when_humans_say_go_below_it(cfg: EngineConfig) -> None:
    cases = dataset()
    tuned = tune_thresholds(cases, cfg)
    assert tuned.thresholds.T_go <= 66
    before = sum(
        case_cost(c.human_verdict, s)
        for c, s in zip(cases, evaluate_cases(cases, cfg), strict=True)
    )
    after = sum(
        case_cost(c.human_verdict, s)
        for c, s in zip(cases, evaluate_cases(cases, tuned), strict=True)
    )
    assert after < before


def test_tuning_never_gets_worse_than_the_current_config_on_its_own_data(cfg: EngineConfig) -> None:
    cases = [*dataset(4), case("odd", "PIVOT", level=2.9), case("odd2", "NO-GO", level=2.6)]
    tuned = tune_thresholds(cases, cfg)

    def total(c: EngineConfig) -> float:
        return sum(
            case_cost(x.human_verdict, s)
            for x, s in zip(cases, evaluate_cases(cases, c), strict=True)
        )

    assert total(tuned) <= total(cfg)


def test_when_the_defaults_are_already_best_they_are_kept(cfg: EngineConfig) -> None:
    cases = [case(f"g{i}", "GO", level=4.2) for i in range(6)] + [
        case(f"n{i}", "NO-GO", level=1.8) for i in range(6)
    ]
    tuned = tune_thresholds(cases, cfg)
    assert (tuned.thresholds.T_go, tuned.thresholds.T_no, tuned.thresholds.floor) == (70, 50, 1.5)


def test_a_false_go_is_avoided_even_at_the_price_of_two_milder_errors(cfg: EngineConfig) -> None:
    # one human NO-GO sits at 3.4 (68 points); two humans call 3.4 items PIVOT-or-GO. Lowering
    # T_go to catch the GO items would create a false GO, which costs 5 > the 2 it would save.
    cases = [case("bad", "NO-GO", level=3.4)] + [
        case(f"ok{i}", "PIVOT", level=3.4) for i in range(1)
    ]
    tuned = tune_thresholds(cases, cfg)
    assert "GO" not in evaluate_cases([cases[0]], tuned)


def test_tuning_is_deterministic(cfg: EngineConfig) -> None:
    a = tune_thresholds(dataset(), cfg).thresholds
    b = tune_thresholds(list(reversed(dataset())), cfg).thresholds
    assert a == b


# ---- weight tuning
def weighted_dataset() -> list[TuningCase]:
    """Feasibility matters to humans: strong items have high feasibility, weak ones low."""
    cases: list[TuningCase] = []
    for i in range(8):
        cases.append(case(f"s{i}", "GO", level=3.0, feasibility=5.0, learning=5.0))
        cases.append(case(f"w{i}", "PIVOT", level=3.6, feasibility=1.6, learning=5.0))
    return cases


def test_weights_stay_valid_and_inside_the_allowed_band(cfg: EngineConfig) -> None:
    tuned = tune_weights(weighted_dataset(), cfg, "portfolio")
    weights: dict[str, float] = {k: v for k, v in tuned.weights["portfolio"].items()}
    assert sum(weights.values()) == pytest.approx(100.0)
    for dim in DIMENSIONS:
        default: float = cfg.weights["portfolio"][dim]  # type: ignore[index]
        assert 0.5 * default - 1e-9 <= weights[dim] <= 1.5 * default + 1e-9
        assert weights[dim] >= 2


def test_weight_tuning_does_not_get_worse_and_leaves_other_goals_alone(cfg: EngineConfig) -> None:
    cases = weighted_dataset()
    tuned = tune_weights(cases, cfg, "portfolio")

    def total(c: EngineConfig) -> float:
        return sum(
            case_cost(x.human_verdict, s)
            for x, s in zip(cases, evaluate_cases(cases, c), strict=True)
        )

    assert total(tuned) <= total(cfg)
    assert tuned.weights["startup"] == cfg.weights["startup"]
    assert tuned.weights["course_project"] == cfg.weights["course_project"]


def test_a_goal_with_too_few_cases_keeps_its_preset(cfg: EngineConfig) -> None:
    few = [case(f"x{i}", "GO", level=4.0, goal="startup") for i in range(3)]
    assert tune_weights(few, cfg, "startup").weights == cfg.weights


# ---- k-fold
def test_folds_partition_the_cases_deterministically_and_stratified() -> None:
    cases = dataset()
    folds = kfold(cases, 4)
    flat = sorted(i for f in folds for i in f)
    assert flat == list(range(len(cases)))
    assert folds == kfold(list(cases), 4)
    counts = [sum(1 for i in f if cases[i].human_verdict == "NO-GO") for f in folds]
    assert max(counts) - min(counts) <= 1


@pytest.mark.parametrize(("n", "k", "expected"), [(10, 5, 5), (3, 5, 3), (2, 2, 2)])
def test_k_is_clamped_to_the_number_of_cases(n: int, k: int, expected: int) -> None:
    cases = [case(f"c{i}", "GO") for i in range(n)]
    assert len(kfold(cases, k)) == expected


def test_k_below_two_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        kfold([case("a", "GO"), case("b", "GO")], 1)


# ---- cross validation and the proposal
def test_cross_validation_reports_default_and_tuned_costs_on_held_out_data(
    cfg: EngineConfig,
) -> None:
    cv = cross_validate(dataset(), cfg, k=4)
    assert cv["folds"] == 4
    assert cv["tuned_cost"] < cv["default_cost"]
    assert cv["tuned_false_go"] <= cv["default_false_go"]
    assert cv == cross_validate(dataset(), cfg, k=4)


def test_proposal_applies_a_real_improvement(cfg: EngineConfig) -> None:
    p = propose(dataset(), cfg, k=4)
    assert p["recommend"] == "apply"
    assert p["thresholds"]["T_go"] < 70
    assert p["weights_version"] == "w2" and p["thresholds_version"] == "t2"
    assert p["after"]["agreement"] > p["before"]["agreement"]
    assert p["cv"]["gain"] >= 0.05


def test_proposal_keeps_the_defaults_for_too_few_cases(cfg: EngineConfig) -> None:
    p = propose(dataset(2), cfg, k=4)
    assert p["recommend"] == "keep_defaults"
    assert "too few" in p["reason"]
    assert p["thresholds"] == {"T_go": 70, "T_no": 50, "floor": 1.5}


def test_proposal_keeps_the_defaults_when_there_is_no_held_out_gain(cfg: EngineConfig) -> None:
    perfect = [case(f"g{i}", "GO", level=4.2) for i in range(12)] + [
        case(f"n{i}", "NO-GO", level=1.8) for i in range(12)
    ]
    p = propose(perfect, cfg, k=4)
    assert p["recommend"] == "keep_defaults"
    assert "no held-out gain" in p["reason"]


def test_proposal_never_changes_anything_outside_the_bounds(cfg: EngineConfig) -> None:
    p = propose(dataset(), cfg, k=4)
    t = p["thresholds"]
    assert BOUNDS["T_go"][0] <= t["T_go"] <= BOUNDS["T_go"][1]
    assert t["T_go"] - t["T_no"] >= 5


# ---- versions and loading
@pytest.mark.parametrize(("old", "new"), [("w1", "w2"), ("t9", "t10"), ("w", "w2"), ("r1", "r2")])
def test_bump_version(old: str, new: str) -> None:
    assert bump_version(old) == new


def test_load_cases_reads_jsonl_and_rejects_bad_rows(tmp_path: Path) -> None:
    good = case("a", "GO")
    path = tmp_path / "cases.jsonl"
    row = {"id": good.id, "input": good.input.model_dump(mode="json"), "human_verdict": "GO"}
    path.write_text(json.dumps(row) + "\n\n")
    assert [c.id for c in load_cases(path)] == ["a"]
    path.write_text(json.dumps({**row, "human_verdict": "MAYBE"}) + "\n")
    with pytest.raises(ValueError):
        load_cases(path)
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path)
