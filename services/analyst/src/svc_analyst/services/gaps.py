"""Gap detection (docs/harness/10 §8).

A gap is a cluster of complaint/request cards inside a niche with
* at least ``min_complaints`` cards from at least ``min_platforms`` distinct platforms, and
* no product card of the niche whose features match: ``cosine(cluster centroid, product) <
  coverage_threshold`` (0.75) for every product.

``product_coverage`` is how many products are merely *related* (cosine >= ``related_threshold``).
``score = complaints * ln(1 + platforms) * recency`` with recency the share of the cluster's
cards published in the last 12 months before ``as_of`` (0 without ``as_of``).
Evidence ids are chunk 0 of each cluster card: a gap never exists without them.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..models.cards import Card
from ..models.domain import Gap, Niche
from .clustering import agglomerate, c_tfidf_terms, dedupe_cards
from .features import months_back
from .text import Embedder, TfidfEmbedder, centroid, cosine, tokenize


@dataclass(frozen=True)
class GapConfig:
    merge_threshold: float = 0.30
    min_complaints: int = 3
    min_platforms: int = 2
    coverage_threshold: float = 0.75
    related_threshold: float = 0.30
    recency_months: int = 12
    description_terms: int = 3


def detect_gaps(
    niches: Sequence[Niche],
    cards: Sequence[Card],
    as_of: date | None,
    config: GapConfig | None = None,
    *,
    embedder: Embedder | None = None,
) -> list[Gap]:
    """Gaps of every niche, sorted by (-score, id)."""
    cfg = config or GapConfig()
    ordered = [
        c
        for c in dedupe_cards(cards)
        if as_of is None or c.published_at is None or c.published_at <= as_of
    ]
    if not ordered or not niches:
        return []
    documents = [c.document for c in ordered]
    vectors = dict(
        zip(
            (c.id for c in ordered),
            (embedder or TfidfEmbedder(documents)).vectorize(documents),
            strict=True,
        )
    )
    by_id = {c.id: c for c in ordered}
    cutoff = months_back(as_of, cfg.recency_months) if as_of else None

    gaps: list[Gap] = []
    for niche in niches:
        members = sorted((by_id[i] for i in set(niche.card_ids) if i in by_id), key=lambda c: c.id)
        complaint_cards = [c for c in members if c.is_complaint]
        products = [c for c in members if c.is_product]
        clusters = agglomerate([vectors[c.id] for c in complaint_cards], cfg.merge_threshold)
        ranked = c_tfidf_terms(
            [[t for i in m for t in tokenize(complaint_cards[i].document)] for m in clusters]
        )
        for member_idx, terms in zip(clusters, ranked, strict=True):
            group = [complaint_cards[i] for i in member_idx]
            platforms = frozenset().union(*(c.all_platforms for c in group))
            if len(group) < cfg.min_complaints or len(platforms) < cfg.min_platforms:
                continue
            center = centroid([vectors[c.id] for c in group])
            sims = [cosine(center, vectors[p.id]) for p in products]
            if any(round(s, 12) >= cfg.coverage_threshold for s in sims):
                continue
            related = sum(1 for s in sims if round(s, 12) >= cfg.related_threshold)
            recent = (
                sum(1 for c in group if c.published_at is not None and c.published_at >= cutoff)
                / len(group)
                if cutoff is not None
                else 0.0
            )
            evidence_ids = [c.evidence_id for c in group]
            top = [t for t, _ in terms[: cfg.description_terms]]
            gaps.append(
                Gap(
                    id="gap_" + hashlib.sha256("|".join(evidence_ids).encode()).hexdigest()[:8],
                    niche_id=niche.id,
                    description=(
                        f"{len(group)} complaints or requests about {', '.join(top)} "
                        f"from {len(platforms)} platforms; no product in the niche addresses them"
                    ),
                    evidence_ids=evidence_ids,
                    complaint_count=len(group),
                    product_coverage=related,
                    score=round(len(group) * math.log(1 + len(platforms)) * recent, 6),
                )
            )
    return sorted(gaps, key=lambda g: (-g.score, g.id))
