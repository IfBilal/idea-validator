"""Deterministic feature-overlap measure for the anti-copy check (no model, no randomness)."""

import re
from collections.abc import Sequence
from typing import Final

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS: Final = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "your",
        "you",
        "are",
        "can",
        "has",
        "have",
        "into",
        "over",
        "per",
        "via",
        "not",
        "but",
        "all",
        "any",
        "its",
        "our",
        "out",
        "use",
        "used",
        "using",
        "app",
        "apps",
        "tool",
        "tools",
        "based",
    ]
)


def tokenize(text: str) -> frozenset[str]:
    """Lowercase word set without stopwords and words shorter than 3 characters."""
    return frozenset(w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOPWORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def feature_overlap(features: Sequence[str], product_text: str) -> float:
    """Jaccard overlap between an idea's core-feature tokens and a product's feature text."""
    return jaccard(tokenize(" ".join(features)), tokenize(product_text))
