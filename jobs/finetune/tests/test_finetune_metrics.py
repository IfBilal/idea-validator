import json
from typing import Any

import pytest

from finetune.metrics import bootstrap_ci, latency_stats, score_outputs

EV = ["card_aaaaaaaa#c0", "card_bbbbbbbb#c1"]


def out(score: Any, ids: Any = None, conf: float = 0.5) -> Any:
    return json.dumps(
        {"score": score, "confidence": conf, "evidence_ids": ids or [EV[0]], "reason": "r"}
    )


def test_mae_within1_valid_and_real_ids() -> None:
    items = [
        {"dimension": "risk", "human": 3.0, "provided": EV, "output": out(3.5)},
        {"dimension": "risk", "human": 2.0, "provided": EV, "output": out(4.5)},
        {"dimension": "learning", "human": 4.0, "provided": EV, "output": "not json"},
        {
            "dimension": "learning",
            "human": 1.0,
            "provided": EV,
            "output": out(1.0, ["card_cccccccc#c0"]),
        },
    ]
    m = score_outputs(items)
    assert m["n"] == 4
    assert m["valid_json_rate"] == 0.75
    assert m["real_evidence_rate"] == pytest.approx(2 / 3)
    # MAE over valid scored outputs: 0.5, 2.5, 0.0
    assert m["mae"] == pytest.approx(1.0)
    assert m["within_1"] == pytest.approx(2 / 3)
    assert m["per_dimension"]["risk"]["mae"] == pytest.approx(1.5)
    assert m["per_dimension"]["learning"]["n"] == 2


def test_abstain_is_valid_but_not_scored() -> None:
    o = json.dumps({"score": None, "confidence": 0, "evidence_ids": [], "reason": "no"})
    m = score_outputs([{"dimension": "risk", "human": 3.0, "provided": EV, "output": o}])
    assert m["valid_json_rate"] == 1.0
    assert m["abstain_rate"] == 1.0
    assert m["mae"] is None


def test_verdict_key_makes_output_invalid() -> None:
    o = json.dumps(
        {"score": 3, "confidence": 0.5, "evidence_ids": [EV[0]], "reason": "r", "verdict": "GO"}
    )
    m = score_outputs([{"dimension": "risk", "human": 3.0, "provided": EV, "output": o}])
    assert m["valid_json_rate"] == 0.0


def test_bootstrap_ci_is_deterministic_and_brackets_mean() -> None:
    errs = [0.0, 0.5, 1.0, 0.5, 2.0, 0.0, 1.5, 0.5]
    lo, hi = bootstrap_ci(errs, seed=42)
    assert (lo, hi) == bootstrap_ci(errs, seed=42)
    assert lo is not None and hi is not None
    assert lo <= sum(errs) / len(errs) <= hi
    assert bootstrap_ci([1.0], seed=1) == (1.0, 1.0)
    assert bootstrap_ci([], seed=1) == (None, None)


def test_latency_percentiles() -> None:
    s = latency_stats([0.1 * i for i in range(1, 101)])
    assert s["p50"] == pytest.approx(5.05, abs=0.1)
    assert s["p95"] == pytest.approx(9.5, abs=0.1)
    assert latency_stats([]) == {"p50": None, "p95": None}
