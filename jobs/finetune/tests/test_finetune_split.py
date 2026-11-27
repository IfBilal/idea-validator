import json
from typing import Any

import pytest

from finetune.split import OverlapError, assert_no_overlap, grouped_split, normalise, similarity


def rows(n_ideas: int = 40, dims: tuple[str, ...] = ("demand_pain", "risk")) -> Any:
    out = []
    for i in range(n_ideas):
        for d in dims:
            out.append(
                {
                    "idea_id": f"idea{i}",
                    "idea_text": f"unique product number {i} for students",
                    "dimension": d,
                }
            )
    return out


def test_same_idea_never_straddles_splits() -> None:
    parts = grouped_split(rows(), test_ideas=set(), val_fraction=0.1, seed=42)
    ids = {k: {r["idea_id"] for r in v} for k, v in parts.items()}
    assert not ids["train"] & ids["val"]
    assert ids["train"] | ids["val"] == {f"idea{i}" for i in range(40)}


def test_val_is_ten_percent_of_train_ideas_deterministic() -> None:
    a = grouped_split(rows(), test_ideas=set(), val_fraction=0.1, seed=42)
    b = grouped_split(rows(), test_ideas=set(), val_fraction=0.1, seed=42)
    assert a == b
    assert len({r["idea_id"] for r in a["val"]}) == 4
    c = grouped_split(rows(), test_ideas=set(), val_fraction=0.1, seed=7)
    assert a != c


def test_test_ideas_are_removed_from_train_and_val() -> None:
    parts = grouped_split(rows(), test_ideas={"idea0", "idea1"}, val_fraction=0.1, seed=42)
    for k in ("train", "val"):
        assert not {r["idea_id"] for r in parts[k]} & {"idea0", "idea1"}


def test_similarity_and_normalise() -> None:
    assert normalise("  Hello,   WORLD! ") == "hello world"
    assert similarity("A CLI tool to export notes", "a cli tool to export notes!") == pytest.approx(
        1.0
    )
    assert similarity("export notes to markdown", "train a neural network on cats") < 0.5


def test_overlap_by_id_fails() -> None:
    labelled = [{"id": "idea3", "idea_text": "totally different words here"}]
    with pytest.raises(OverlapError, match="id"):
        assert_no_overlap(rows(), labelled)


def test_overlap_by_text_similarity_fails() -> None:
    labelled = [{"id": "L1", "idea_text": "Unique product number 3 for students!"}]
    with pytest.raises(OverlapError, match="similar"):
        assert_no_overlap(rows(), labelled)


def test_no_overlap_passes() -> None:
    labelled = [{"id": "L1", "idea_text": "a robotic lawn mower subscription for farmers"}]
    assert_no_overlap(rows(), labelled)


def test_empty_labelled_set_is_an_error_when_required() -> None:
    with pytest.raises(OverlapError, match="empty"):
        assert_no_overlap(rows(), [], require_nonempty=True)


def test_fails_closed_on_missing_text() -> None:
    with pytest.raises(OverlapError):
        assert_no_overlap([{"idea_id": "a", "dimension": "risk"}], [{"id": "L1", "idea_text": "x"}])


def test_json_roundtrip_safe() -> None:
    json.dumps(grouped_split(rows(), test_ideas=set(), val_fraction=0.1, seed=1))


def test_huge_text_is_bounded() -> None:
    big = "word " * 500_000
    assert similarity(big, big) == pytest.approx(1.0)
