"""Competitor-name rule (docs/harness/09 §3): no competitor may be named unless it is a KB card.

Names are found heuristically (capitalised words / phrases that are not sentence openers) and only
checked for claims that talk about competition, so ordinary capitalised words do not cause
false rejections. Matching against KB card names is case-insensitive and fuzzy (ratio >= 0.9).
"""

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9

_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]*(?: [A-Z][A-Za-z0-9]*)*")
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n\s*)$")
_CUES = re.compile(
    r"\b(?:competitors?|competing|alternatives?|rivals?|incumbents?"
    r"|existing (?:tools?|products?|apps?)|such as|similar to|versus|vs\.?|instead of"
    r"|rather than)\b",
    re.IGNORECASE,
)
_COMPETITION_DIMENSIONS = frozenset({"competition", "differentiation"})
_EDGE_WORDS = frozenset(
    "i in on at the a an and or of by for to from with it its this that these those my our "  # noqa: SIM905
    "january february march april may june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday".split()
)
# data sources, not competitors (GitHub is deliberately absent: it is also a real product)
_IGNORED_PHRASES = frozenset({"hacker news", "product hunt", "y combinator", "yc", "reddit", "hn"})


def _trim(words: list[str]) -> list[str]:
    while words and words[0].lower() in _EDGE_WORDS:
        words = words[1:]
    while words and words[-1].lower() in _EDGE_WORDS:
        words = words[:-1]
    return words


def extract_names(text: str) -> list[str]:
    """Candidate product/company names in ``text``, de-duplicated, in order of appearance."""
    found: list[str] = []
    for match in _CANDIDATE.finditer(text):
        words = _trim(match.group(0).split(" "))
        if not words:
            continue
        at_sentence_start = _SENTENCE_START.search(text[: match.start()]) is not None
        if at_sentence_start and len(match.group(0).split(" ")) == 1:
            continue
        phrase = " ".join(words)
        if phrase.lower() in _IGNORED_PHRASES or len(phrase) < 2:
            continue
        if phrase not in found:
            found.append(phrase)
    return found


def _normalise(name: str) -> str:
    return " ".join(name.lower().split())


def matches_any(name: str, kb_names: Iterable[str]) -> bool:
    """True when ``name`` equals (or is within fuzzy ratio 0.9 of) some KB card name."""
    wanted = _normalise(name)
    for candidate in kb_names:
        other = _normalise(candidate)
        if wanted == other or SequenceMatcher(None, wanted, other).ratio() >= FUZZY_THRESHOLD:
            return True
    return False


def names_competitors(text: str, dimension: str | None) -> list[str]:
    """Names that must be KB cards: only for competition-flavoured claims, else nothing."""
    if dimension not in _COMPETITION_DIMENSIONS and _CUES.search(text) is None:
        return []
    return extract_names(text)
