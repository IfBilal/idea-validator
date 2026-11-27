import json
from pathlib import Path
from typing import Any

import pytest

from finetune.compare import (
    Generation,
    compare_models,
    failure_case,
    render_markdown,
)
from finetune.prompting import PromptFactory, make_example, repo_root

EV = [("card_8f21aa01#c2", "quote")]


def make_rows(n: int = 6) -> list[dict[str, Any]]:
    f = PromptFactory(repo_root() / "rubrics", repo_root() / "prompts")
    out = []
    for i in range(n):
        dim = "risk" if i % 2 else "demand_pain"
        ex = make_example(
            f,
            idea_id=f"i{i}",
            idea=f"idea {i}",
            rubric_id=f"{dim}@v1",
            evidence=EV,
            features={},
            score=float(i % 5),
            confidence=0.7,
            cited=[EV[0][0]],
            reason="r",
        )
        ex["meta"].update(
            hand_checked=True,
            checked_by="bilal",
            checked_at="t",
            original_label=float(i % 5),
            final_label=float(i % 5),
        )
        out.append(ex)
    return out


class Fixed:
    """A fake model: answers with human + offset, records the prompts it was given."""

    def __init__(self, name: str, offset: float, cost: float = 0.0, valid: bool = True) -> None:
        self.name = name
        self.offset = offset
        self.cost = cost
        self.valid = valid
        self.seen: list[tuple[str, str, int]] = []

    def generate(self, *, system: str, user: str, max_tokens: int) -> Generation:
        self.seen.append((system, user, max_tokens))
        human = float(len(self.seen) - 1) % 5  # matches make_rows scores in order
        text = (
            json.dumps(
                {
                    "score": min(5.0, human + self.offset),
                    "confidence": 0.5,
                    "evidence_ids": [EV[0][0]],
                    "reason": "r",
                }
            )
            if self.valid
            else "garbage"
        )
        return Generation(text=text, seconds=0.5, cost_usd=self.cost)


def test_all_models_get_identical_prompts_and_budget() -> None:
    rows = make_rows()
    a, b = Fixed("a", 0.0), Fixed("b", 1.0)
    compare_models(rows, {"a": a, "b": b}, max_tokens=800, seed=42)
    assert a.seen == b.seen
    assert {t for _, _, t in a.seen} == {800}


def test_metrics_and_latency_per_model() -> None:
    rows = make_rows()
    res = compare_models(
        rows, {"good": Fixed("good", 0.0), "off": Fixed("off", 1.0, cost=0.002)}, 800, seed=42
    )
    assert res["models"]["good"]["mae"] == pytest.approx(0.0)
    assert res["models"]["off"]["mae"] == pytest.approx(1.0)
    assert res["models"]["off"]["cost_per_score_usd"] == pytest.approx(0.002)
    assert res["models"]["good"]["latency_s"]["p50"] == pytest.approx(0.5)
    assert res["n_test"] == 6
    assert res["models"]["good"]["mae_ci95"][0] is not None


def test_refuses_unchecked_examples() -> None:
    rows = make_rows()
    rows[0]["meta"]["hand_checked"] = False
    with pytest.raises(ValueError, match="hand-checked"):
        compare_models(rows, {"a": Fixed("a", 0.0)}, 800, seed=42)


def test_refuses_synthetic_and_empty() -> None:
    rows = make_rows()
    rows[0]["meta"]["synthetic"] = True
    with pytest.raises(ValueError, match="synthetic"):
        compare_models(rows, {"a": Fixed("a", 0.0)}, 800, seed=42)
    with pytest.raises(ValueError, match="empty"):
        compare_models([], {"a": Fixed("a", 0.0)}, 800, seed=42)


def test_failure_case_picks_the_dimension_where_finetuned_loses_most() -> None:
    rows = make_rows(10)
    res = compare_models(
        rows, {"base": Fixed("base", 0.5), "finetuned": Fixed("finetuned", 0.0)}, 800, seed=1
    )
    # make the fine-tuned model worse than hosted on risk only
    res["models"]["hosted"] = {
        "per_dimension": {
            "risk": {"n": 5, "mae": 0.2, "within_1": 1.0},
            "demand_pain": {"n": 5, "mae": 9.0, "within_1": 0.0},
        }
    }
    res["models"]["finetuned"]["per_dimension"]["risk"]["mae"] = 1.4
    case = failure_case(res, train_counts={"risk": {"2": 3}})
    assert case["dimension"] == "risk"
    assert case["finetuned_mae"] == 1.4 and case["hosted_mae"] == 0.2
    assert case["train_examples_per_level"] == {"2": 3}


def test_failure_case_reports_honestly_when_fine_tuning_never_lost() -> None:
    rows = make_rows(10)
    res = compare_models(
        rows, {"base": Fixed("base", 2.0), "finetuned": Fixed("finetuned", 0.0)}, 800, seed=1
    )
    case = failure_case(res, train_counts={})
    assert case["found"] is False


def test_markdown_table_has_the_six_metrics_and_cis(tmp_path: Path) -> None:
    rows = make_rows()
    res = compare_models(
        rows, {"base": Fixed("base", 1.0), "finetuned": Fixed("finetuned", 0.0)}, 800, seed=1
    )
    md = render_markdown(res)
    for col in (
        "MAE vs human",
        "Within 1 point",
        "Valid JSON",
        "Real evidence ids",
        "Latency p50/p95",
        "Cost / score",
    ):
        assert col in md
    assert "95% CI" in md
    assert "| base |" in md and "| finetuned |" in md
