"""Computed features of docs/harness/09 §4. Pure functions: no I/O, no clock, no randomness.

Every function documents its formula. ``as_of`` is always passed in (the data's own reference
date), so the same input always gives the same output.
"""

import calendar
import math
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from ..models.cards import COMPLAINT_KINDS, Card
from ..models.domain import Gap
from .text import tokenize

# ---- evidence-derived ------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRef:
    """One cited evidence chunk, resolved against its card."""

    evidence_id: str
    card_id: str
    kind: str | None
    platforms: frozenset[str]
    published_at: date | None
    score: float | None = None  # rerank score if known; None = already vetted by the caller

    @classmethod
    def of(cls, card: Card, evidence_id: str, score: float | None = None) -> "EvidenceRef":
        return cls(
            evidence_id=evidence_id,
            card_id=card.id,
            kind=card.kind,
            platforms=card.all_platforms,
            published_at=card.published_at,
            score=score,
        )


def relevant(evidence: Iterable[EvidenceRef], tau: float | None = None) -> list[EvidenceRef]:
    """Distinct (by evidence id) items that passed relevance: ``score >= tau``; unscored pass."""
    seen: dict[str, EvidenceRef] = {}
    for item in evidence:
        if item.evidence_id in seen:
            continue
        if tau is not None and item.score is not None and item.score < tau:
            continue
        seen[item.evidence_id] = item
    return list(seen.values())


def n_sources(evidence: Iterable[EvidenceRef], tau: float | None = None) -> int:
    """Distinct card ids among the relevant evidence."""
    return len({e.card_id for e in relevant(evidence, tau)})


def n_platforms(evidence: Iterable[EvidenceRef], tau: float | None = None) -> int:
    """Distinct ``card_sources.platform`` over the cards of the relevant evidence."""
    return len(frozenset().union(*(e.platforms for e in relevant(evidence, tau))))


def complaint_count(evidence: Iterable[EvidenceRef], tau: float | None = None) -> int:
    """Relevant evidence chunks whose card kind is ``complaint`` or ``request``."""
    return sum(1 for e in relevant(evidence, tau) if e.kind in COMPLAINT_KINDS)


def months_back(day: date, months: int) -> date:
    """``day`` minus ``months`` calendar months, clamping the day (Feb 29 -> Feb 28)."""
    year, month0 = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def recency_share(
    evidence: Iterable[EvidenceRef],
    as_of: date,
    months: int = 12,
    tau: float | None = None,
) -> float:
    """Share of relevant evidence published on or after ``as_of - months`` (inclusive).

    Unknown dates are not recent; dates after ``as_of`` are. Empty evidence gives 0.0.
    """
    items = relevant(evidence, tau)
    if not items:
        return 0.0
    cutoff = months_back(as_of, months)
    recent = sum(1 for e in items if e.published_at is not None and e.published_at >= cutoff)
    return recent / len(items)


# ---- niche-derived ---------------------------------------------------------------------------

_STATUS_VALUE = {"rising": 0.0, "flat": 0.5, "saturated": 1.0}


def trend_growth(niche_slope: float | None) -> float:
    """The niche's normalised 6-month mention slope (``delta / mean``); 0.0 if no niche."""
    return 0.0 if niche_slope is None else niche_slope


def size_percentile(size: int, sizes: Sequence[int]) -> float:
    """Mid-rank percentile of ``size`` among ``sizes`` in [0, 1]; a lone niche is 0.5."""
    n = len(sizes)
    if n <= 1:
        return 0.5
    less = sum(1 for s in sizes if s < size)
    equal = sum(1 for s in sizes if s == size)
    return (less + 0.5 * (max(equal, 1) - 1)) / (n - 1)


def niche_saturation(status: str | None, size: int | None, sizes: Sequence[int]) -> float:
    """``(status_value + size_percentile) / 2`` with saturated=1, flat=0.5, rising=0.

    An unknown niche is neutral (0.5).
    """
    status_value = _STATUS_VALUE.get(status, 0.5) if status else 0.5
    percentile = 0.5 if size is None else size_percentile(size, sizes)
    return (status_value + percentile) / 2


# ---- competition -----------------------------------------------------------------------------

COMPETITOR_SIMILARITY = 0.80
_STAGE_WEIGHT = {
    "dead": 0.0,
    "unknown": 0.3,
    "launched": 0.3,
    "seed": 0.5,
    "series_a": 0.7,
    "series_b_plus": 0.9,
    "acquired": 1.0,
}
_FUNDING_CAP = 1e9
_STARS_CAP = 1e5


class Scored(NamedTuple):
    """An existing product card compared with the idea."""

    card: Card
    cosine: float
    jaccard: float
    same_user: bool


def n_competitors(scored: Iterable[Scored], threshold: float = COMPETITOR_SIMILARITY) -> int:
    """Product cards with ``cosine(idea, card) >= threshold`` for the same target user."""
    return sum(1 for s in scored if s.same_user and s.cosine >= threshold)


def card_strength(card: Card) -> float:
    """Mean of three terms in [0, 1]: ``ln(1+funding)/ln(1+1e9)``, stage weight, and
    ``ln(1+stars)/ln(1+1e5)``. Stage weights: dead 0, unknown/launched 0.3, seed 0.5,
    series_a 0.7, series_b_plus 0.9, acquired 1.
    """
    funding = min(1.0, math.log1p(card.funding_usd or 0) / math.log1p(_FUNDING_CAP))
    stars = min(1.0, math.log1p(card.github_stars or 0) / math.log1p(_STARS_CAP))
    return (funding + _STAGE_WEIGHT.get(card.stage, 0.3) + stars) / 3


def competitor_strength(
    scored: Iterable[Scored], threshold: float = COMPETITOR_SIMILARITY
) -> float:
    """Mean ``card_strength`` of the competitors (0.0 when there are none)."""
    strengths = [card_strength(s.card) for s in scored if s.same_user and s.cosine >= threshold]
    return math.fsum(strengths) / len(strengths) if strengths else 0.0


def max_similarity(scored: Iterable[Scored]) -> float:
    """Largest per-product ``max(cosine, core-feature Jaccard)``; either signal alone counts."""
    return max((max(s.cosine, s.jaccard) for s in scored), default=0.0)


DUPLICATE_SIMILARITY = 0.85  # docs/harness/09 section 4: same core features for the same user


def duplicate_of(scored: Iterable[Scored], threshold: float = DUPLICATE_SIMILARITY) -> str | None:
    """Id of the existing product card that already does this for the same user, or ``None``.

    Input of the M11 duplicate gate, so it is computed here from stored cards (never written by a
    model): the card with the largest ``max(cosine, jaccard) >= threshold`` among the same user's
    cards; ties go to the smaller id so the answer does not depend on retrieval order.
    """
    best: tuple[float, str] | None = None
    for item in scored:
        strength = max(item.cosine, item.jaccard)
        if not item.same_user or strength < threshold:
            continue
        if best is None or (-strength, item.card.id) < (-best[0], best[1]):
            best = (strength, item.card.id)
    return best[1] if best else None


GAP_RELEVANCE = 0.25


def unaddressed_gap_count(
    gaps: Iterable[Gap],
    relevance: Mapping[str, float],
    cited_evidence: Collection[str],
    min_relevance: float = GAP_RELEVANCE,
) -> int:
    """Gaps of the niche (unaddressed by construction) that concern this idea: cosine of the
    idea to the gap text >= ``min_relevance`` (inclusive) or the idea cites one of its evidence.
    """
    cited = set(cited_evidence)
    return sum(
        1
        for g in gaps
        if relevance.get(g.id, 0.0) >= min_relevance or cited.intersection(g.evidence_ids)
    )


# ---- target user -----------------------------------------------------------------------------

_FOR_PHRASE = re.compile(r"\bfor\s+([^.,;:!?()\n]+)", re.IGNORECASE)
_PHRASE_END = re.compile(r"\b(that|who|which|with|to|so|by|using|and|in|on|from)\b", re.IGNORECASE)


def target_users(text: str) -> frozenset[str]:
    """Tokens of every ``for <audience>`` phrase (cut at that/who/with/to/and/...)."""
    tokens: set[str] = set()
    for match in _FOR_PHRASE.finditer(text):
        phrase = _PHRASE_END.split(match.group(1), maxsplit=1)[0]
        tokens.update(tokenize(phrase))
    return frozenset(tokens)


def same_user(idea_users: frozenset[str], card_users: frozenset[str]) -> bool:
    """Same target user: shared token; an unknown side cannot rule a product out."""
    if not idea_users or not card_users:
        return True
    return bool(idea_users & card_users)


# ---- feasibility -----------------------------------------------------------------------------

_BASE_HOURS = {"S": 60.0, "M": 160.0, "L": 400.0}
_COMPUTE_ORDER = {"laptop": 0, "free_gpu": 1, "paid": 2}


def skill_overlap(profile_skills: Iterable[str], required: Iterable[str]) -> float:
    """``|profile.skills & required| / |required|`` (case-insensitive, de-duplicated).

    No required skills are known: neutral 0.5 (it is neither evidence for nor against).
    """
    need = {s.strip().lower() for s in required if s.strip()}
    if not need:
        return 0.5
    have = {s.strip().lower() for s in profile_skills}
    return len(need & have) / len(need)


def available_hours(hours_per_week: int, weeks_available: int, team_size: int) -> int:
    """``hours_per_week * weeks_available * team_size``."""
    return hours_per_week * weeks_available * team_size


def estimated_effort_hours(scope: str, overlap: float, gpu_needed: bool, compute: str) -> float:
    """``base[scope] * (1 + 0.5 * (1 - skill_overlap)) * compute_factor``.

    base: S 60, M 160, L 400. compute_factor is 1.5 when the idea needs a GPU and the student
    only has a laptop, otherwise 1.
    """
    if scope not in _BASE_HOURS:
        raise ValueError(f"scope must be S, M or L, got {scope!r}")
    if not 0.0 <= overlap <= 1.0:
        raise ValueError(f"skill_overlap must be within [0, 1], got {overlap}")
    factor = 1.5 if gpu_needed and compute == "laptop" else 1.0
    return _BASE_HOURS[scope] * (1 + 0.5 * (1 - overlap)) * factor


def compute_fit(required: str, available: str) -> bool:
    """``required compute <= profile compute`` on laptop < free_gpu < paid."""
    if required not in _COMPUTE_ORDER or available not in _COMPUTE_ORDER:
        raise ValueError(f"unknown compute level: {required!r} / {available!r}")
    return _COMPUTE_ORDER[required] <= _COMPUTE_ORDER[available]


def _phrases(words: Iterable[str]) -> re.Pattern[str]:
    body = "|".join(sorted((w for w in words), key=lambda w: (-len(w), w)))
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])", re.IGNORECASE)


_PAID_COMPUTE = _phrases(
    ["a100", "h100", "v100", "multi-gpu", "gpu cluster", "70b", "65b", "pretrain", "pre-train"]
)
_GPU = _phrases(
    [
        "gpu",
        "gpus",
        "cuda",
        "fine-tune",
        "fine-tuning",
        "finetune",
        "finetuning",
        "deep learning",
        "diffusion model",
        "stable diffusion",
        "train a model",
        "training a model",
        "model training",
    ]
)


def needs_gpu(text: str) -> bool:
    """Keyword test for GPU work (training, fine-tuning, CUDA, diffusion...)."""
    return bool(_GPU.search(text) or _PAID_COMPUTE.search(text))


def required_compute(text: str) -> str:
    """``paid`` for A100/70B/pretraining keywords, ``free_gpu`` for other GPU work, else laptop."""
    if _PAID_COMPUTE.search(text):
        return "paid"
    return "free_gpu" if _GPU.search(text) else "laptop"


_COMPLEX = _phrases(
    [
        "marketplace",
        "real-time",
        "realtime",
        "mobile app",
        "pipeline",
        "payments?",
        "multi-tenant",
        "blockchain",
        "video",
        "recommendation engine",
    ]
)
_CLAUSE_SPLIT = re.compile(r"[,;.\n]|\band\b", re.IGNORECASE)
_SCOPE_S_MAX = 3
_SCOPE_M_MAX = 8


def infer_scope(text: str) -> str:
    """Scope from the idea text: ``points = clauses + 2 * complexity keywords``.

    A clause is a ``, ; .`` / ``and`` separated segment of at least two words; complexity
    keywords: marketplace, real-time, mobile app, pipeline, payments, multi-tenant, blockchain,
    video, recommendation engine. ``S`` for points <= 3, ``M`` for <= 8, else ``L``.
    """
    clauses = sum(1 for part in _CLAUSE_SPLIT.split(text) if len(part.split()) >= 2)
    points = clauses + 2 * len(_COMPLEX.findall(text))
    if points <= _SCOPE_S_MAX:
        return "S"
    return "M" if points <= _SCOPE_M_MAX else "L"


# ---- skills ----------------------------------------------------------------------------------

SKILL_VOCABULARY: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "java",
    "kotlin",
    "swift",
    "rust",
    "c++",
    "c#",
    "ruby",
    "php",
    "sql",
    "postgresql",
    "mongodb",
    "redis",
    "react",
    "vue",
    "angular",
    "node",
    "django",
    "flask",
    "fastapi",
    "docker",
    "kubernetes",
    "aws",
    "git",
    "linux",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "pandas",
    "machine learning",
    "nlp",
    "computer vision",
    "llm",
    "html",
    "css",
    "flutter",
    "android",
    "ios",
    "figma",
    "excel",
)
_SKILL_ALIASES = {"golang": "go", "js": "javascript", "ts": "typescript", "postgres": "postgresql"}


def _skill_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9+#]){re.escape(term)}(?![a-z0-9+#])", re.IGNORECASE)


def extract_required_skills(text: str, profile_skills: Iterable[str]) -> list[str]:
    """Skills named in the idea: the fixed vocabulary, aliases (golang -> go) and any profile
    skill. Sorted, lower-case, unique. Ambiguous words like "go" only count via the profile.
    """
    found: set[str] = set()
    terms = set(SKILL_VOCABULARY) | {s.strip().lower() for s in profile_skills if s.strip()}
    for term in terms:
        if _skill_pattern(term).search(text):
            found.add(term)
    for alias, canonical in _SKILL_ALIASES.items():
        if _skill_pattern(alias).search(text):
            found.add(canonical)
    return sorted(found)


# ---- risk ------------------------------------------------------------------------------------

_AUTH = r"(?:log[- ]?in|logged[- ]in|sign[- ]in|authenticated|paywall)"
_RISK_PATTERNS: dict[str, re.Pattern[str]] = {
    "health_data": _phrases(
        [
            "patient",
            "patients",
            "medical",
            "health data",
            "health record",
            "health records",
            "hipaa",
            "diagnosis",
            "medication",
            "symptom",
            "symptoms",
            "therapy",
        ]
    ),
    "minors": _phrases(
        ["minors", "kids", "children", "child", "under 13", "under 18", "teen", "teens", "k-12"]
    ),
    "finance_trading": _phrases(
        [
            "trading",
            "stock trading",
            "forex",
            "day trading",
            "stock market",
            "brokerage",
            "hedge fund",
            "investment advice",
        ]
    ),
    "pii": _phrases(
        [
            "email address",
            "email addresses",
            "phone number",
            "phone numbers",
            "ssn",
            "passport",
            "home address",
            "date of birth",
            "national id",
            "credit card",
            "pii",
        ]
    ),
    "scraping_logged_in": re.compile(
        rf"(?:scrap\w*|crawl\w*)\b.{{0,60}}\b{_AUTH}|{_AUTH}.{{0,60}}\b(?:scrap\w*|crawl\w*)",
        re.IGNORECASE,
    ),
}
BLOCKER_FLAGS = frozenset({"scraping_logged_in"})  # the project rules forbid logging in to sites

PLATFORMS: tuple[str, ...] = (
    "twitter",
    "x api",
    "instagram",
    "tiktok",
    "whatsapp",
    "slack",
    "discord",
    "notion",
    "shopify",
    "telegram",
    "youtube",
    "spotify",
    "openai",
    "google maps",
    "zoom",
    "salesforce",
    "facebook",
    "reddit",
)
_PLATFORM = _phrases(PLATFORMS)


def _platforms_named(text: str) -> set[str]:
    return {m.group(0).lower() for m in _PLATFORM.finditer(text)}


def platform_dependency(text: str) -> bool:
    """True when the idea names exactly one third-party platform (its value hangs on one API)."""
    return len(_platforms_named(text)) == 1


def risk_flags(text: str) -> list[str]:
    """Sorted unique flags from keyword/regex lists: health_data, minors, finance_trading, pii,
    scraping_logged_in, tos_dependent_api (a single named platform). A flag in
    ``BLOCKER_FLAGS`` also adds ``blocker`` (the risk rubric scores a blocker 0).
    """
    flags = {name for name, pattern in _RISK_PATTERNS.items() if pattern.search(text)}
    if platform_dependency(text):
        flags.add("tos_dependent_api")
    if flags & BLOCKER_FLAGS:
        flags.add("blocker")
    return sorted(flags)


def risk_blocker(flags: Iterable[str]) -> str | None:
    """The blocker flag itself (a fixed token such as ``scraping_logged_in``), else ``None``.

    Input of the M11 risk gate: a code from ``BLOCKER_FLAGS``, never free text, so a model cannot
    invent a blocker.
    """
    found = sorted(set(flags) & BLOCKER_FLAGS)
    return found[0] if found else None


# ---- learning --------------------------------------------------------------------------------


def skill_novelty(
    required: Iterable[str], profile_skills: Iterable[str], interests: Iterable[str]
) -> int:
    """Required skills that are in ``profile.interests`` or not in ``profile.skills``."""
    need = {s.strip().lower() for s in required if s.strip()}
    skills = {s.strip().lower() for s in profile_skills}
    wanted = {s.strip().lower() for s in interests}
    return len({s for s in need if s in wanted or s not in skills})


def cv_signal(required: Iterable[str], in_demand: Iterable[str], trend: float) -> float:
    """``0.5 * share(required skills in the in-demand list) + 0.5 * clamp(0.5 + trend, 0, 1)``."""
    need = {s.strip().lower() for s in required if s.strip()}
    demand = {s.strip().lower() for s in in_demand}
    share = len(need & demand) / len(need) if need else 0.0
    trend_term = min(1.0, max(0.0, 0.5 + trend))
    return 0.5 * share + 0.5 * trend_term
