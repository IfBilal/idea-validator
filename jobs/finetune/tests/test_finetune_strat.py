from typing import Any

from finetune.strat import MIN_CELL, stratification_report


def ex(dim: str, level: float | None) -> Any:
    return {"dimension": dim, "level": level}


def test_counts_and_empty_cells() -> None:
    data = [ex("risk", 3.0)] * 8 + [ex("risk", 1.0)] * 2
    rep = stratification_report(data)
    assert rep["counts"]["risk"]["3"] == 8
    assert rep["counts"]["risk"]["1"] == 2
    assert "risk@1" in rep["under_filled"]
    assert "risk@3" not in rep["under_filled"]
    assert "demand_pain@0" in rep["empty"]
    assert rep["ok"] is False


def test_half_points_bucket_to_level_and_abstain_counted() -> None:
    rep = stratification_report([ex("risk", 3.5), ex("risk", None)])
    assert rep["counts"]["risk"]["3"] == 1
    assert rep["abstain"]["risk"] == 1


def test_ok_when_every_cell_full() -> None:
    dims = ["demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"]
    data = [ex(d, float(level)) for d in dims for level in range(6) for _ in range(MIN_CELL)]
    assert stratification_report(data)["ok"] is True
