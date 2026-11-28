"""Retrieval metrics on hand-computed examples (graded relevance, card-level)."""

import math

import pytest

from eval.retrieval.metrics import dedupe, ndcg_at, recall_at, reciprocal_rank_at

REL = {"a": 2, "b": 1, "c": 1}


def test_dedupe_keeps_first_occurrence_order() -> None:
    assert dedupe(["x", "y", "x", "z", "y"]) == ["x", "y", "z"]


def test_recall_counts_relevant_cards_found_in_the_top_k() -> None:
    assert recall_at(["a", "x", "b"], REL, 3) == pytest.approx(2 / 3)
    assert recall_at(["a", "x", "b"], REL, 1) == pytest.approx(1 / 3)
    assert recall_at([], REL, 5) == 0.0


def test_recall_is_none_when_nothing_is_relevant() -> None:
    assert recall_at(["a"], {}, 5) is None


def test_reciprocal_rank_is_one_over_the_first_relevant_position() -> None:
    assert reciprocal_rank_at(["x", "y", "b"], REL, 10) == pytest.approx(1 / 3)
    assert reciprocal_rank_at(["a"], REL, 10) == 1.0
    assert reciprocal_rank_at(["x", "y", "b"], REL, 2) == 0.0


def test_ndcg_is_one_for_the_ideal_order_and_lower_otherwise() -> None:
    assert ndcg_at(["a", "b", "c"], REL, 10) == pytest.approx(1.0)
    ideal = 3 / 1 + 1 / math.log2(3) + 1 / math.log2(4)  # gains 2**g - 1 = 3, 1, 1
    swapped = 1 / 1 + 3 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at(["b", "a", "c"], REL, 10) == pytest.approx(swapped / ideal)
    assert ndcg_at(["x", "y"], REL, 10) == 0.0
