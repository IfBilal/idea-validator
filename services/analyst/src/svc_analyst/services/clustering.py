"""Niche clustering: deterministic agglomerative clustering on TF-IDF cosine.

Algorithm (no randomness; ``SEED = 42`` is kept for anything stochastic added later):
1. cards are sorted by id, vectorised (``Embedder``, TF-IDF by default);
2. centroid-linkage agglomeration: repeatedly merge the two most similar clusters while
   ``cosine(centroids) >= merge_threshold`` (ties broken by the smallest card index);
3. clusters smaller than ``min_size`` join the nearest big cluster if their cosine is at least
   ``attach_threshold``, otherwise their cards are *unassigned* (noise);
4. each niche is named by its top-3 c-TF-IDF terms and gets a stable id derived from them;
5. monthly series (launches = ``product`` cards, mentions = every other kind but ``repo``),
   slopes over the last ``window`` months, and a status.

The production option is HDBSCAN on dense embeddings (docs/harness/10 §8); the ``Embedder``
seam and the result model stay the same.
"""

import hashlib
import heapq
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from common.schemas.base import Message

from ..models.cards import Card
from ..models.domain import MonthStat, Niche, NicheStatus
from .text import Embedder, TfidfEmbedder, Vector, centroid, cosine, tokenize

SEED = 42


@dataclass(frozen=True)
class ClusterConfig:
    merge_threshold: float = 0.15
    min_size: int = 3
    attach_threshold: float = 0.05
    window: int = 6
    rising_slope: float = 0.10
    saturated_slope: float = 0.02
    saturated_ratio: float = 1.5
    keyword_count: int = 10
    name_terms: int = 3


class Analysis(Message):
    as_of: date | None
    niches: list[Niche]
    unassigned: list[str]


# ---- series and slopes -----------------------------------------------------------------------


def least_squares_slope(ys: Sequence[float]) -> float:
    """Slope of the least-squares line through ``(0, y0), (1, y1) ...``; 0.0 for < 2 points."""
    n = len(ys)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = math.fsum(ys) / n
    sxx = math.fsum((i - mean_x) ** 2 for i in range(n))
    sxy = math.fsum((i - mean_x) * (y - mean_y) for i, y in enumerate(ys))
    return sxy / sxx


def normalised_slope(ys: Sequence[float]) -> float:
    """``least_squares_slope / mean``; 0.0 when the mean is 0 (no activity, no trend)."""
    if not ys:
        return 0.0
    mean = math.fsum(ys) / len(ys)
    if mean == 0.0:
        return 0.0
    return round(least_squares_slope(ys) / mean, 6)


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _from_index(index: int) -> date:
    year, month0 = divmod(index, 12)
    return date(year, month0 + 1, 1)


def window_months(as_of: date, window: int) -> list[date]:
    """The ``window`` consecutive first-of-month dates ending with ``as_of``'s month."""
    end = _month_index(as_of)
    return [_from_index(i) for i in range(end - window + 1, end + 1)]


def monthly_series(cards: Sequence[Card], as_of: date | None) -> list[MonthStat]:
    """Contiguous monthly (launches, mentions) from the first dated card to ``as_of``'s month.

    launches: ``product`` cards; mentions: complaint / request / discussion / unknown kind;
    ``repo`` cards and undated cards are ignored; cards after ``as_of``'s month are ignored.
    """
    if as_of is None:
        return []
    last = _month_index(as_of)
    launches: Counter[int] = Counter()
    mentions: Counter[int] = Counter()
    for card in cards:
        if card.published_at is None or card.kind == "repo":
            continue
        index = _month_index(card.published_at)
        if index > last:
            continue
        (launches if card.kind == "product" else mentions)[index] += 1
    seen = launches.keys() | mentions.keys()
    if not seen:
        return []
    return [
        MonthStat(month=_from_index(i), launches=launches[i], mentions=mentions[i])
        for i in range(min(seen), last + 1)
    ]


def classify(
    activity_slope: float,
    size: int,
    sizes: Sequence[int],
    launches: int,
    mentions: int,
    config: ClusterConfig,
) -> NicheStatus:
    """rising: slope >= 0.10. saturated: slope <= 0.02 and size in the top quartile and
    launches/mentions >= 1.5 (all inclusive). Otherwise flat.
    """
    if activity_slope >= config.rising_slope:
        return "rising"
    ordered = sorted(sizes)
    quartile_floor = ordered[min(len(ordered) - 1, (3 * len(ordered) + 3) // 4)]
    ratio = (math.inf if launches > 0 else 0.0) if mentions == 0 else launches / mentions
    if (
        activity_slope <= config.saturated_slope
        and size >= quartile_floor
        and ratio >= config.saturated_ratio
    ):
        return "saturated"
    return "flat"


# ---- agglomeration ---------------------------------------------------------------------------


def agglomerate(vectors: Sequence[Vector], threshold: float) -> list[list[int]]:
    """Centroid-linkage clustering of ``vectors``; returns index lists (sorted, by first index).

    Deterministic: ties are broken by the smallest member index. Pairs are only considered for
    clusters that share a term (cosine of disjoint vectors is 0).
    """
    n = len(vectors)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    cents: dict[int, Vector] = {i: vectors[i] for i in range(n)}
    postings: dict[str, set[int]] = {}
    for i, vec in enumerate(vectors):
        for term in vec:
            postings.setdefault(term, set()).add(i)

    heap: list[tuple[float, int, int, int, int]] = []

    def push(a: int, b: int) -> None:
        sim = round(cosine(cents[a], cents[b]), 12)
        if sim >= threshold and sim > 0.0:
            x, y = (a, b) if members[a][0] < members[b][0] else (b, a)
            heapq.heappush(heap, (-sim, members[x][0], members[y][0], x, y))

    for i in range(n):
        neighbours = {j for term in vectors[i] for j in postings[term] if j > i}
        for j in sorted(neighbours):
            push(i, j)

    next_id = n
    while heap:
        _, _, _, a, b = heapq.heappop(heap)
        if a not in members or b not in members:
            continue
        merged = sorted(members.pop(a) + members.pop(b))
        old_a, old_b = cents.pop(a), cents.pop(b)
        for term in old_a.keys() | old_b.keys():
            postings[term].discard(a)
            postings[term].discard(b)
        new = next_id
        next_id += 1
        members[new] = merged
        cents[new] = centroid([vectors[i] for i in merged])
        candidates: set[int] = set()
        for term in cents[new]:
            candidates |= postings.setdefault(term, set())
        for other in sorted(candidates):
            push(new, other)
        for term in cents[new]:
            postings[term].add(new)
    return sorted(members.values(), key=lambda m: m[0])


# ---- naming ----------------------------------------------------------------------------------


def c_tfidf_terms(class_tokens: Sequence[Sequence[str]]) -> list[list[tuple[str, float]]]:
    """Class-based TF-IDF: ``tf(t, c) * ln(1 + A / f(t))`` with ``A`` the mean class length and
    ``f(t)`` the term's total count over all classes. Each list is sorted by (-weight, term).
    """
    counts = [Counter(tokens) for tokens in class_tokens]
    total: Counter[str] = Counter()
    for c in counts:
        total.update(c)
    mean_len = math.fsum(sum(c.values()) for c in counts) / len(counts) if counts else 0.0
    ranked: list[list[tuple[str, float]]] = []
    for c in counts:
        length = sum(c.values()) or 1
        weights = [(t, (c[t] / length) * math.log(1 + mean_len / total[t])) for t in c]
        ranked.append(sorted(weights, key=lambda tw: (-tw[1], tw[0])))
    return ranked


def niche_ids(clusters: Sequence[tuple[Sequence[str], Sequence[str]]]) -> list[str]:
    """``niche_`` + 8 hex of sha256 of the sorted top terms; on a collision the smallest member id
    is mixed in. Pass the clusters in a deterministic order.
    """
    taken: set[str] = set()
    out: list[str] = []
    for terms, members in clusters:
        base = hashlib.sha256("|".join(sorted(terms)).encode()).hexdigest()[:8]
        candidate, salt = base, 0
        while candidate in taken:
            salt += 1
            seed = f"{base}|{min(members) if members else ''}|{salt}"
            candidate = hashlib.sha256(seed.encode()).hexdigest()[:8]
        taken.add(candidate)
        out.append(f"niche_{candidate}")
    return out


# ---- the whole pipeline ----------------------------------------------------------------------


def analyse(
    cards: Sequence[Card],
    config: ClusterConfig | None = None,
    *,
    as_of: date | None = None,
    embedder: Embedder | None = None,
) -> Analysis:
    """Cluster ``cards`` into niches. ``as_of`` defaults to the newest publication date."""
    cfg = config or ClusterConfig()
    ordered = sorted(cards, key=lambda c: c.id)
    if as_of is None:
        dates = [c.published_at for c in ordered if c.published_at is not None]
        as_of = max(dates) if dates else None
    if not ordered:
        return Analysis(as_of=as_of, niches=[], unassigned=[])

    documents = [c.document for c in ordered]
    vectors = (embedder or TfidfEmbedder(documents)).vectorize(documents)
    clusters = agglomerate(vectors, cfg.merge_threshold)

    big = [m for m in clusters if len(m) >= cfg.min_size]
    big_centroids = [centroid([vectors[i] for i in m]) for m in big]
    kept: list[list[int]] = [list(m) for m in big]
    unassigned: list[int] = []
    for small in (m for m in clusters if len(m) < cfg.min_size):
        mini = centroid([vectors[i] for i in small])
        best, best_sim = -1, 0.0
        for k, cent in enumerate(big_centroids):
            sim = cosine(mini, cent)
            if sim > best_sim:
                best, best_sim = k, sim
        if best >= 0 and best_sim >= cfg.attach_threshold:
            kept[best].extend(small)
        else:
            unassigned.extend(small)
    kept = [sorted(m) for m in kept]

    ranked = c_tfidf_terms([[t for i in m for t in tokenize(documents[i])] for m in kept])
    terms = [[t for t, _ in r[: cfg.keyword_count]] for r in ranked]
    ids = niche_ids(
        [
            (t[: cfg.name_terms], [ordered[i].id for i in m])
            for t, m in zip(terms, kept, strict=True)
        ]
    )

    drafts = []
    for niche_id, member, keywords in zip(ids, kept, terms, strict=True):
        cards_in = [ordered[i] for i in member]
        series = monthly_series(cards_in, as_of)
        by_month = {s.month: s for s in series}
        window = window_months(as_of, cfg.window) if as_of else []
        mentions = [by_month[m].mentions if m in by_month else 0 for m in window]
        launches = [by_month[m].launches if m in by_month else 0 for m in window]
        drafts.append((niche_id, cards_in, keywords, series, mentions, launches))

    sizes = [len(d[1]) for d in drafts]
    niches: list[Niche] = []
    for niche_id, cards_in, keywords, series, mentions, launches in drafts:
        activity = [m + launch for m, launch in zip(mentions, launches, strict=True)]
        activity_slope = normalised_slope(activity)
        niches.append(
            Niche(
                id=niche_id,
                name=", ".join(keywords[: cfg.name_terms]),
                size=len(cards_in),
                trend_slope=normalised_slope(mentions),
                activity_slope=activity_slope,
                status=classify(
                    activity_slope, len(cards_in), sizes, sum(launches), sum(mentions), cfg
                ),
                keywords=keywords,
                card_ids=[c.id for c in cards_in],
                series=series,
            )
        )
    niches.sort(key=lambda n: (-n.size, n.id))
    return Analysis(
        as_of=as_of, niches=niches, unassigned=sorted(ordered[i].id for i in unassigned)
    )
