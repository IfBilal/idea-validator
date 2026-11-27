"""Realistic M6 stand-ins: 6 niches, 85+ cards, 5 platforms, 18 months. Fully deterministic."""

import hashlib
import random
from dataclasses import dataclass
from datetime import date

from svc_analyst.models.cards import Card

SEED = 42
PLATFORM_CYCLE = [
    ("reddit",),
    ("hackernews", "reddit"),
    ("github",),
    ("producthunt", "hackernews"),
    ("yc",),
    ("reddit", "github"),
]


@dataclass(frozen=True)
class NicheSpec:
    slug: str
    core: tuple[str, ...]
    features: tuple[str, ...]  # what products offer
    pain_a: tuple[str, ...]  # complaint theme no product addresses
    pain_b: tuple[str, ...]
    customer: str
    launch_months: tuple[str, ...]
    mention_months: tuple[str, ...]  # 'YYYY-MM', one entry per mention card


NICHES = (
    NicheSpec(
        "invoicing",
        ("invoice", "freelancer", "billing", "client"),
        ("template", "pdf", "recurring", "tax", "dashboard"),
        ("late", "payment", "reminder", "chasing"),
        ("currency", "conversion", "multicurrency", "rounding"),
        "freelance designers",
        ("2025-09", "2026-02", "2026-05"),
        (
            "2025-10",
            "2025-12",
            "2026-02",
            "2026-03",
            "2026-04",
            "2026-04",
            "2026-05",
            "2026-05",
            "2026-06",
            "2026-06",
            "2026-06",
        ),
    ),
    NicheSpec(
        "notes",
        ("note", "notebook", "markdown", "sync"),
        ("tag", "search", "backlink", "editor", "plugin"),
        ("slow", "export", "lockin", "format"),
        ("offline", "conflict", "merge", "mobile"),
        "students",
        tuple(f"{m}" for m in ("2025-08", "2025-12"))
        + tuple(
            m
            for m in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")
            for _ in range(2)
        ),
        ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"),
    ),
    NicheSpec(
        "mlops",
        ("gpu", "training", "cluster", "model"),
        ("scheduler", "dashboard", "inference", "checkpoint", "registry"),
        ("cost", "idle", "spot", "billing"),
        ("debug", "oom", "memory", "profiler"),
        "ml engineers",
        ("2025-07", "2025-11", "2026-03"),
        (
            "2025-08",
            "2025-10",
            "2026-01",
            "2026-01",
            "2026-02",
            "2026-03",
            "2026-03",
            "2026-04",
            "2026-05",
            "2026-05",
        ),
    ),
    NicheSpec(
        "languages",
        ("language", "vocabulary", "lesson", "learner"),
        ("flashcard", "streak", "audio", "quiz", "course"),
        ("pronunciation", "feedback", "accent", "speaking"),
        ("grammar", "explanation", "context", "sentence"),
        "language learners",
        ("2025-09", "2026-01", "2026-04"),
        (
            "2025-09",
            "2025-11",
            "2026-01",
            "2026-01",
            "2026-01",
            "2026-02",
            "2026-02",
            "2026-03",
            "2026-04",
            "2026-05",
            "2026-06",
        ),
    ),
    NicheSpec(
        "fitness",
        ("workout", "gym", "routine", "tracker"),
        ("calorie", "wearable", "plan", "reminder", "progress"),
        ("injury", "form", "posture", "coaching"),
        ("plateau", "periodization", "deload", "strength"),
        "gym beginners",
        ("2025-10", "2026-02", "2026-05"),
        (
            "2025-12",
            "2026-01",
            "2026-02",
            "2026-02",
            "2026-03",
            "2026-04",
            "2026-04",
            "2026-05",
            "2026-06",
            "2026-06",
        ),
    ),
    NicheSpec(
        "recipes",
        ("recipe", "meal", "grocery", "pantry"),
        ("shopping", "nutrition", "scaling", "import", "planner"),
        ("allergy", "substitute", "dietary", "intolerance"),
        ("leftover", "waste", "expiry", "fridge"),
        "home cooks",
        ("2025-08", "2026-01", "2026-04"),
        (
            "2025-11",
            "2026-01",
            "2026-02",
            "2026-03",
            "2026-03",
            "2026-04",
            "2026-05",
            "2026-06",
            "2026-06",
        ),
    ),
)

_KINDS = ("complaint", "request", "discussion", "complaint", "request")
_STAGES = ("launched", "seed", "series_a", "launched", "unknown")


def card_id(label: str) -> str:
    return "card_" + hashlib.sha256(label.encode()).hexdigest()[:8]


def _day(month: str, n: int) -> date:
    year, mon = month.split("-")
    return date(int(year), int(mon), 1 + (n * 3) % 27)


def build_cards() -> list[Card]:
    rng = random.Random(SEED)  # noqa: S311  deterministic fixtures
    cards: list[Card] = []
    for spec in NICHES:
        for i, month in enumerate(spec.launch_months):
            feats = rng.sample(spec.features, 3)
            core = rng.sample(spec.core, 2)
            name = f"{spec.slug.title()}Pro{i}"
            cards.append(
                _card(
                    f"{spec.slug}-product-{i}",
                    name,
                    f"{name} is a {core[0]} {core[1]} tool for {spec.customer}",
                    f"{' '.join(feats)} {spec.slug} {core[0]} {core[1]} product",
                    spec,
                    kind="product",
                    stage=_STAGES[i % len(_STAGES)],
                    funding=(i * 750_000) or None,
                    stars=(i * 900) or None,
                    month=month,
                    n=i,
                )
            )
        for i, month in enumerate(spec.mention_months):
            theme = spec.pain_a if i % 3 != 2 else spec.pain_b
            core = rng.sample(spec.core, 2)
            pain = rng.sample(theme, 3)
            kind = _KINDS[i % len(_KINDS)]
            if kind == "discussion":
                # discussions are mentions but not complaints: keep them on-niche, off-theme
                text = f"{spec.slug} {core[0]} {core[1]} thread what do you use {spec.slug}"
            else:
                text = f"{spec.slug} {core[0]} {core[1]} {' '.join(pain)} is frustrating"
            cards.append(
                _card(
                    f"{spec.slug}-mention-{i}",
                    f"{spec.slug} thread {i}",
                    text[:280],
                    text,
                    spec,
                    kind=kind,
                    stage="unknown",
                    funding=None,
                    stars=None,
                    month=month,
                    n=i + 5,
                )
            )
    return sorted(cards, key=lambda c: c.id)


def _card(
    label: str,
    name: str,
    summary: str,
    text: str,
    spec: NicheSpec,
    *,
    kind: str,
    stage: str,
    funding: int | None,
    stars: int | None,
    month: str,
    n: int,
) -> Card:
    platforms = PLATFORM_CYCLE[n % len(PLATFORM_CYCLE)]
    return Card.model_validate(
        {
            "id": card_id(label),
            "name": name,
            "summary": summary,
            "text": text,
            "customer": spec.customer if kind == "product" else None,
            "niche_tags": [spec.slug],
            "stage": stage,
            "funding_usd": funding,
            "github_stars": stars,
            "source": platforms[0],
            "platforms": list(platforms),
            "kind": kind,
            "published_at": _day(month, n),
        }
    )


def outlier_cards() -> list[Card]:
    """Three unrelated one-off cards: they must not form a niche."""
    words = [
        ("quantum", "teleporter", "cheese"),
        ("zeppelin", "marmalade", "origami"),
        ("volcano", "tuba", "lanternfish"),
    ]
    return [
        Card.model_validate(
            {
                "id": card_id(f"outlier-{i}"),
                "name": " ".join(w),
                "summary": " ".join(w),
                "text": " ".join(w),
                "source": "hackernews",
                "platforms": ["hackernews"],
                "kind": "discussion",
                "published_at": date(2026, 3, 5),
            }
        )
        for i, w in enumerate(words)
    ]


def synthetic_cards(n: int, seed: int = SEED) -> list[Card]:
    """``n`` cards in 25 topical groups, for performance and shuffle tests."""
    rng = random.Random(seed)  # noqa: S311  deterministic fixtures
    topics = [[f"t{t}w{w}" for w in range(12)] for t in range(25)]
    out: list[Card] = []
    for i in range(n):
        words = topics[i % 25]
        text = " ".join(rng.sample(words, 6))
        out.append(
            Card.model_validate(
                {
                    "id": card_id(f"synthetic-{i}"),
                    "name": f"s{i}",
                    "summary": text[:200],
                    "text": text,
                    "source": "reddit",
                    "platforms": ["reddit"],
                    "kind": ("product", "complaint", "request", "discussion")[i % 4],
                    "published_at": date(2025 + (i % 18) // 12, 1 + (i % 18) % 12, 1),
                }
            )
        )
    return out
