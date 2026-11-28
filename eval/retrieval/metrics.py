"""Ranking metrics over card ids with graded relevance (grade 2 = highly relevant, 1 = relevant)."""

import math
from collections.abc import Mapping, Sequence


def dedupe(ids: Sequence[str]) -> list[str]:
    """Several chunks of one card count once, at the card's best rank."""
    return list(dict.fromkeys(ids))


def recall_at(ranked: Sequence[str], relevant: Mapping[str, int], k: int) -> float | None:
    if not relevant:
        return None
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank_at(ranked: Sequence[str], relevant: Mapping[str, int], k: int) -> float:
    for position, card_id in enumerate(ranked[:k], start=1):
        if card_id in relevant:
            return 1 / position
    return 0.0


def _dcg(grades: Sequence[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def ndcg_at(ranked: Sequence[str], relevant: Mapping[str, int], k: int) -> float:
    ideal = _dcg(sorted(relevant.values(), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return _dcg([relevant.get(card_id, 0) for card_id in ranked[:k]]) / ideal
