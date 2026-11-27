import json
from pathlib import Path
from typing import Any

import pytest

from finetune.dataset import DatasetError, build_dataset
from finetune.prompting import PromptFactory, make_example, repo_root
from finetune.schema import load_jsonl

DIMS = ("demand_pain", "competition", "differentiation", "feasibility", "risk", "learning")
TOPICS = [
    "recipe sharing app",
    "invoice tool",
    "bus tracker",
    "plant watering sensor",
    "language flashcards",
    "gym buddy matcher",
    "thesis citation manager",
    "bike repair marketplace",
    "podcast clipper",
    "dorm laundry alerts",
    "lab inventory scanner",
    "carpool planner",
    "budget splitter",
    "exam seat finder",
    "campus lost and found",
    "meal prep planner",
    "code review trainer",
    "sleep diary",
    "tutor marketplace",
    "event ticket swap",
]


def examples(n_ideas: int = 20, checked: int = 0) -> list[dict[str, Any]]:
    f = PromptFactory(repo_root() / "rubrics", repo_root() / "prompts")
    out = []
    for i in range(n_ideas):
        for d in DIMS:
            ex = make_example(
                f,
                idea_id=f"idea{i}",
                idea=f"a {TOPICS[i % len(TOPICS)]} variant {i} for university students",
                rubric_id=f"{d}@v1",
                evidence=[("card_8f21aa01#c2", "q")],
                features={},
                score=float(i % 6),
                confidence=0.6,
                cited=["card_8f21aa01#c2"],
                reason="r",
            )
            if i < checked:
                ex["meta"].update(
                    hand_checked=True,
                    checked_by="bilal+taimoor",
                    checked_at="t",
                    original_label=float(i % 6),
                    final_label=float(i % 6),
                )
            out.append(ex)
    return out


LABELLED = [{"id": "L1", "idea_text": "a robotic lawn mower subscription for farmers"}]


def run(tmp_path: Path, rows: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return build_dataset(rows, labelled=LABELLED, out_dir=tmp_path, seed=42, **kw)


def test_hand_checked_ideas_become_the_test_set_and_never_leak(tmp_path: Path) -> None:
    stats = run(tmp_path, examples(20, checked=4), min_checked=1)
    train = load_jsonl(str(tmp_path / "train.jsonl"))
    val = load_jsonl(str(tmp_path / "val.jsonl"))
    test = load_jsonl(str(tmp_path / "test.jsonl"))
    assert all(e.meta.hand_checked for e in test)
    assert not any(e.meta.hand_checked for e in train + val)
    ids = [{e.meta.idea_id for e in part} for part in (train, val, test)]
    assert not (ids[0] & ids[1]) and not (ids[0] & ids[2]) and not (ids[1] & ids[2])
    assert stats["counts"]["test"] == 24
    assert stats["overlap_with_labelled"] == "none (asserted)"


def test_too_few_hand_checked_fails_unless_draft_only(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="hand-checked"):
        run(tmp_path, examples(20, checked=2), min_checked=60)
    assert not (tmp_path / "test.jsonl").exists()


def test_draft_only_mode_writes_train_and_val_but_marks_itself(tmp_path: Path) -> None:
    stats = run(tmp_path, examples(20, checked=0), min_checked=0, allow_no_test=True)
    assert stats["status"] == "draft-only: no hand-checked test set yet"
    assert not (tmp_path / "test.jsonl").exists() or (tmp_path / "test.jsonl").read_text() == ""


def test_labelled_overlap_fails_the_build(tmp_path: Path) -> None:
    rows = examples(20, checked=4)
    for r in rows:
        if r["meta"]["idea_id"] == "idea7":
            r["meta"]["idea_text"] = "A robotic lawn mower subscription for farmers"
    with pytest.raises(AssertionError, match="similar"):
        run(tmp_path, rows, min_checked=1)


def test_synthetic_rows_are_refused(tmp_path: Path) -> None:
    rows = examples(20)
    rows[0]["meta"]["synthetic"] = True
    with pytest.raises(DatasetError, match="synthetic"):
        run(tmp_path, rows, min_checked=0, allow_no_test=True)


def test_readme_and_stats_document_counts_and_provenance(tmp_path: Path) -> None:
    run(tmp_path, examples(20, checked=4), min_checked=1)
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "seed: 42" in readme and "demand_pain" in readme
    assert "provenance" in readme.lower()
    stats = json.loads((tmp_path / "stats.json").read_text())
    assert stats["seed"] == 42 and "stratification" in stats


def test_invalid_examples_fail_the_build(tmp_path: Path) -> None:
    rows = examples(20)
    rows[3]["messages"][2]["content"] = "not json"
    with pytest.raises(Exception, match="target"):
        run(tmp_path, rows, min_checked=0, allow_no_test=True)
