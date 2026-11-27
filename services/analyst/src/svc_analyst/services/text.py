"""Deterministic text features: tokeniser, TF-IDF vectors, cosine and Jaccard.

No network, no model download, no randomness. ``Embedder`` is the seam where M6's dense
embeddings can be plugged in later: any object with ``vectorize(texts) -> list[Vector]`` works,
because a dense vector is just a mapping ``"0".."d" -> float``.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

Vector = Mapping[str, float]

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "make",
        "me",
        "more",
        "most",
        "my",
        "no",
        "not",
        "of",
        "on",
        "one",
        "only",
        "or",
        "our",
        "out",
        "over",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "up",
        "us",
        "use",
        "used",
        "using",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


def stem(word: str) -> str:
    """Tiny plural stripper: ``companies -> company``, ``invoices -> invoice``; keeps ss/us/is."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lower-case, drop stopwords and 1-character tokens, strip plurals. Order is preserved."""
    out: list[str] = []
    for raw in _WORD.findall(text.lower()):
        if raw in STOPWORDS:
            continue
        token = stem(raw)
        if len(token) >= 2:
            out.append(token)
    return out


def dot(a: Vector, b: Vector) -> float:
    shared = sorted(a.keys() & b.keys())
    return math.fsum(a[k] * b[k] for k in shared)


def norm(a: Vector) -> float:
    return math.sqrt(math.fsum(v * v for v in a.values()))


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity in [0, 1] for non-negative vectors; 0.0 if either is empty."""
    if not a or not b:
        return 0.0
    denominator = norm(a) * norm(b)
    if denominator == 0.0:
        return 0.0
    return min(1.0, dot(a, b) / denominator)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """``|A & B| / |A | B|``; 0.0 when both sets are empty."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def normalize(a: Vector) -> dict[str, float]:
    length = norm(a)
    if length == 0.0:
        return {}
    return {k: a[k] / length for k in sorted(a)}


def centroid(vectors: Sequence[Vector]) -> dict[str, float]:
    """L2-normalised sum of the vectors (sums in sorted-term order: byte-stable)."""
    total: dict[str, list[float]] = {}
    for vector in vectors:
        for key, value in vector.items():
            total.setdefault(key, []).append(value)
    return normalize({k: math.fsum(v) for k, v in total.items()})


class Embedder(Protocol):
    """Turns texts into vectors. Same text in, same vector out."""

    def vectorize(self, texts: Sequence[str]) -> list[Vector]: ...


class TfidfEmbedder:
    """Smoothed TF-IDF: ``idf = ln((1 + N) / (1 + df)) + 1``, raw term counts, L2-normalised.

    Fitted on a corpus once; terms never seen in the corpus get the maximum idf (``df = 0``).
    The corpus order does not matter (document frequencies only).
    """

    def __init__(self, corpus: Sequence[str]) -> None:
        df: Counter[str] = Counter()
        for text in corpus:
            df.update(set(tokenize(text)))
        self._n = len(corpus)
        self._df = df

    def idf(self, term: str) -> float:
        return math.log((1 + self._n) / (1 + self._df.get(term, 0))) + 1.0

    def vectorize(self, texts: Sequence[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for text in texts:
            counts = Counter(tokenize(text))
            weighted = {t: counts[t] * self.idf(t) for t in sorted(counts)}
            vectors.append(normalize(weighted))
        return vectors
