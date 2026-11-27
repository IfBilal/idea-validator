"""Exact and near-duplicate removal that also records every platform a card was seen on.

Three passes, in order, over the cards sorted by (published date, url): same canonical URL, same
content hash, then MinHash near-duplicates (Jaccard of the name+summary shingles >= threshold).
Candidates come from banded LSH, and every candidate is confirmed with the exact Jaccard, so the
threshold is exact and inclusive and a result never depends on LSH luck at a verified pair.

The first card in the sort order represents its group. Sorting first makes the result independent
of the order the scouts delivered the items, which is what makes a re-run add nothing.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from ingestion.minhash import MinHasher, jaccard
from ingestion.models import CardDraft, PlannedCard

# under this many shingles a name+summary is too short to call anything a near-duplicate
MIN_SHINGLES = 4
_WORD = re.compile(r"\w+", re.UNICODE)


def shingles(text: str) -> frozenset[str]:
    """Word 2-grams of the lower-cased text; a single word is its own shingle."""
    words = _WORD.findall(text.lower())
    if not words:
        return frozenset()
    if len(words) == 1:
        return frozenset(words)
    return frozenset(f"{a} {b}" for a, b in pairwise(words))


@dataclass(frozen=True)
class DedupeConfig:
    threshold: float = 0.9
    num_perm: int = 128
    bands: int = 32
    seed: int = 1

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {self.threshold}")
        if self.bands < 1 or self.num_perm < 1 or self.num_perm % self.bands:
            raise ValueError(f"bands ({self.bands}) must divide num_perm ({self.num_perm})")
        rows = self.num_perm // self.bands
        if (1 / self.bands) ** (1 / rows) > self.threshold:
            # banded LSH finds a pair with probability ~50% at (1/bands)^(1/rows); a threshold
            # below that would silently miss many true near-duplicates
            raise ValueError(
                f"bands={self.bands} with num_perm={self.num_perm} cannot reach "
                f"threshold {self.threshold}: use more bands"
            )


@dataclass(frozen=True)
class DedupePlan:
    cards: list[PlannedCard]
    duplicates: int  # raw items folded into another card


def _order(card: CardDraft) -> tuple[date, str, str, date, str, str]:
    return (
        card.published_at or date.max,
        card.url,
        card.source,
        card.fetched_at,
        card.name,
        card.content_hash,
    )


def dedupe(cards: Sequence[CardDraft], config: DedupeConfig) -> DedupePlan:
    hasher = MinHasher(num_perm=config.num_perm, seed=config.seed)
    rows = config.num_perm // config.bands
    reps: list[CardDraft] = []
    sets: list[frozenset[str]] = []
    platforms: list[dict[str, None]] = []
    merged: list[int] = []
    by_url: dict[str, int] = {}
    by_hash: dict[str, int] = {}
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = {}

    def fold(into: int, card: CardDraft) -> None:
        platforms[into].setdefault(card.source)
        merged[into] += 1

    for card in sorted(cards, key=_order):
        target = by_url.get(card.url)
        if target is None:
            target = by_hash.get(card.content_hash)
        words = shingles(card.name + " " + card.summary)
        keys: list[tuple[int, tuple[int, ...]]] = []
        if target is None and len(words) >= MIN_SHINGLES:
            sig = hasher.signature(words)
            keys = [(b, sig[b * rows : (b + 1) * rows]) for b in range(config.bands)]
            candidates = sorted({i for k in keys for i in buckets.get(k, ())})
            for i in candidates:
                if jaccard(words, sets[i]) >= config.threshold:
                    target = i
                    break
        if target is not None:
            fold(target, card)
            continue
        index = len(reps)
        reps.append(card)
        sets.append(words)
        platforms.append({card.source: None})
        merged.append(0)
        by_url[card.url] = index
        by_hash[card.content_hash] = index
        for k in keys:
            buckets.setdefault(k, []).append(index)

    planned = [
        PlannedCard(card=card, platforms=tuple(platforms[i]), merged=merged[i])
        for i, card in enumerate(reps)
    ]
    return DedupePlan(cards=planned, duplicates=sum(merged))
