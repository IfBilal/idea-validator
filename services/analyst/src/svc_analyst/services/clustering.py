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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from pydantic import Field

from common.schemas.base import Message

from ..models.cards import Card
from ..models.domain import MonthStat, Niche, NicheStatus
from .text import Embedder, TfidfEmbedder, Vector, centroid, cosine, dot, normalize, tokenize

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
    min_months: int = 3  # fewer months of data than this is never rising or saturated
    id_match_threshold: float = 0.85  # new cluster keeps an old niche id above this cosine


class Analysis(Message):
    as_of: date | None
    niches: list[Niche]
    unassigned: list[str]
    ignored_future: list[str] = Field(default_factory=list)


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
    *,
    months: int | None = None,
) -> NicheStatus:
    """rising: slope >= 0.10. saturated: slope <= 0.02 and size in the top quartile and
    launches/mentions >= 1.5 (all inclusive). Otherwise flat. A niche with fewer than
    ``min_months`` months of data (``months``, when given) is always flat.
    """
    if months is not None and months < config.min_months:
        return "flat"
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

_TOP_TERMS = 30  # terms per cluster used to find merge candidates
_NEIGHBOURS = 12  # best candidates kept per cluster: bounds the heap to O(n)


def _top_terms(vec: Vector) -> list[tuple[str, float]]:
    return sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_TERMS]


def agglomerate(
    vectors: Sequence[Vector], threshold: float, *, df_cap: int | None = None
) -> list[list[int]]:
    """Centroid-linkage clustering of ``vectors``; returns index lists (sorted, by first index).

    Deterministic (ties: smallest member index). Sub-quadratic: merge candidates for a cluster
    come from an inverted index over each cluster's top-30 terms, skipping terms shared by more
    than ``df_cap`` clusters (default ``max(100, 2*sqrt(n))``). Only the 12 best-scoring
    candidates per cluster are kept, which bounds the heap to O(n); their similarity is the
    exact cosine of the L2-normalised centroids and pairs below ``threshold`` never merge.
    Clusters that only share very common terms are therefore not considered (an approximation
    that TF-IDF down-weighting makes harmless).
    """
    n = len(vectors)
    cap = df_cap if df_cap is not None else max(100, int(2 * math.sqrt(n)))
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    cents: dict[int, dict[str, float]] = {i: normalize(vectors[i]) for i in range(n)}
    tops: dict[int, list[tuple[str, float]]] = {i: _top_terms(cents[i]) for i in range(n)}
    postings: dict[str, set[int]] = {}
    for i in range(n):
        for term, _ in tops[i]:
            postings.setdefault(term, set()).add(i)
    heap: list[tuple[float, int, int, int, int]] = []

    def push_candidates(a: int, only_after: bool) -> None:
        acc: dict[int, float] = {}
        for term, weight in tops[a]:
            bucket = postings[term]
            if len(bucket) > cap:
                continue
            for b in bucket:
                if b == a or (only_after and b < a):
                    continue
                acc[b] = acc.get(b, 0.0) + weight * cents[b].get(term, 0.0)
        best = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:_NEIGHBOURS]
        for b, partial in best:
            if partial < threshold * 0.5:
                break
            sim = round(min(1.0, dot(cents[a], cents[b])), 12)  # centroids are unit length
            if sim >= threshold and sim > 0.0:
                x, y = (a, b) if members[a][0] < members[b][0] else (b, a)
                heapq.heappush(heap, (-sim, members[x][0], members[y][0], x, y))

    for i in range(n):
        push_candidates(i, only_after=True)

    next_id = n
    while heap:
        _, _, _, a, b = heapq.heappop(heap)
        if a not in members or b not in members:
            continue
        merged = sorted(members.pop(a) + members.pop(b))
        for gone in (a, b):
            for term, _ in tops.pop(gone):
                postings[term].discard(gone)
        total: dict[str, float] = {}
        for old in (cents.pop(a), cents.pop(b)):
            for term, weight in old.items():
                total[term] = total.get(term, 0.0) + weight
        new = next_id
        next_id += 1
        members[new] = merged
        cents[new] = normalize(total)
        tops[new] = _top_terms(cents[new])
        for term, _ in tops[new]:
            postings.setdefault(term, set()).add(new)
        push_candidates(new, only_after=False)
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


def niche_ids(
    clusters: Sequence[tuple[Sequence[str], Sequence[str]]], taken: Iterable[str] = ()
) -> list[str]:
    """``niche_`` + 8 hex of sha256 of the sorted top terms; on a collision the smallest member id
    is mixed in. Pass the clusters in a deterministic order.
    """
    used = {t.removeprefix("niche_") for t in taken}
    out: list[str] = []
    for terms, members in clusters:
        base = hashlib.sha256("|".join(sorted(terms)).encode()).hexdigest()[:8]
        candidate, salt = base, 0
        while candidate in used:
            salt += 1
            seed = f"{base}|{min(members) if members else ''}|{salt}"
            candidate = hashlib.sha256(seed.encode()).hexdigest()[:8]
        used.add(candidate)
        out.append(f"niche_{candidate}")
    return out


# ---- the whole pipeline ----------------------------------------------------------------------


def dedupe_cards(cards: Iterable[Card]) -> list[Card]:
    """One card per id, sorted by id. Duplicates (the same card seen on several platforms) keep
    the longest text and the union of all platforms; the result does not depend on input order.
    """
    groups: dict[str, list[Card]] = {}
    for card in cards:
        groups.setdefault(card.id, []).append(card)
    out: list[Card] = []
    for card_id in sorted(groups):
        group = groups[card_id]
        if len(group) == 1:
            out.append(group[0])
            continue
        best = sorted(group, key=lambda c: (-len(c.text), c.source, c.name, c.text))[0]
        platforms = sorted(frozenset().union(*(c.all_platforms for c in group)))
        out.append(best.model_copy(update={"platforms": platforms}))
    return out


def _match_ids(
    centroids: Sequence[Vector],
    previous: Sequence[Niche],
    vectors: dict[str, Vector],
    threshold: float,
) -> dict[int, str]:
    """Greedy one-to-one match of new clusters to old niches by centroid cosine >= threshold."""
    pairs: list[tuple[float, int, str]] = []
    for old in previous:
        old_vectors = [vectors[i] for i in old.card_ids if i in vectors]
        if not old_vectors:
            continue
        old_centroid = centroid(old_vectors)
        for k, new_centroid in enumerate(centroids):
            sim = round(cosine(old_centroid, new_centroid), 12)
            if sim >= threshold:
                pairs.append((-sim, k, old.id))
    pairs.sort()
    matched: dict[int, str] = {}
    used: set[str] = set()
    for _, k, old_id in pairs:
        if k not in matched and old_id not in used:
            matched[k] = old_id
            used.add(old_id)
    return matched


def analyse(
    cards: Sequence[Card],
    config: ClusterConfig | None = None,
    *,
    as_of: date | None,
    previous: Sequence[Niche] = (),
    embedder: Embedder | None = None,
) -> Analysis:
    """Cluster ``cards`` into niches.

    ``as_of`` comes from the caller (the KB snapshot date); it is never derived from the data,
    so one bad date cannot move every window. Cards dated after it are ignored and reported in
    ``ignored_future``. ``None`` means no date is known: series stay empty and statuses flat.
    ``previous`` are the niches of the last run: a new cluster whose centroid has cosine >=
    ``id_match_threshold`` with an old niche keeps its id.
    """
    cfg = config or ClusterConfig()
    unique = dedupe_cards(cards)
    ignored = [
        c.id for c in unique if as_of is not None and c.published_at and c.published_at > as_of
    ]
    skipped = set(ignored)
    ordered = [c for c in unique if c.id not in skipped]
    if not ordered:
        return Analysis(as_of=as_of, niches=[], unassigned=[], ignored_future=ignored)

    documents = [c.document for c in ordered]
    all_vectors = (embedder or TfidfEmbedder(documents)).vectorize(documents)
    vectors = all_vectors
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
    new_centroids = [centroid([vectors[i] for i in m]) for m in kept]
    by_id = {c.id: v for c, v in zip(ordered, vectors, strict=True)}
    matched = _match_ids(new_centroids, previous, by_id, cfg.id_match_threshold)
    fresh = [k for k in range(len(kept)) if k not in matched]
    fresh_ids = niche_ids(
        [(terms[k][: cfg.name_terms], [ordered[i].id for i in kept[k]]) for k in fresh],
        taken=[*(o.id for o in previous), *matched.values()],
    )
    ids = dict(matched)
    ids.update(zip(fresh, fresh_ids, strict=True))

    drafts = []
    for k, member in enumerate(kept):
        cards_in = [ordered[i] for i in member]
        series = monthly_series(cards_in, as_of)
        by_month = {s.month: s for s in series}
        window = window_months(as_of, cfg.window) if as_of else []
        mentions = [by_month[m].mentions if m in by_month else 0 for m in window]
        launches = [by_month[m].launches if m in by_month else 0 for m in window]
        drafts.append((ids[k], cards_in, terms[k], series, mentions, launches))

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
                    activity_slope,
                    len(cards_in),
                    sizes,
                    sum(launches),
                    sum(mentions),
                    cfg,
                    months=len(series),
                ),
                keywords=keywords,
                card_ids=[c.id for c in cards_in],
                series=series,
            )
        )
    niches.sort(key=lambda n: (-n.size, n.id))
    return Analysis(
        as_of=as_of,
        niches=niches,
        unassigned=sorted(ordered[i].id for i in unassigned),
        ignored_future=ignored,
    )
