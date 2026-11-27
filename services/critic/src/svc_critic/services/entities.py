"""Named entities in a claim must also appear in the cited evidence (CLAUDE.md hard rule 2).

The lexical judge ignores proper nouns, so "Asana charges $10" could be "supported" by a quote
about Trello. This check closes that gap for every claim, whatever its dimension: a capitalised
token (a likely product or company) or a name after a competition cue ("such as asana") must be
found, as a whole word or phrase, in a cited evidence text or card name (a card name may also
match fuzzily, ratio >= 0.9, as in the competitor rule).

It is a heuristic that errs on the side of rejection, the safe direction for a claim checker: an
unsupported claim is dropped, never rewritten.
"""

import re
from collections.abc import Iterable

from .competitors import matches_any

_WORD = r"[A-Za-z][A-Za-z0-9&'-]*"
_CAPITALISED = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9&'-]*(?: [A-Z][A-Za-z0-9&'-]*)*")
_MIXED_CASE = re.compile(r"(?<![A-Za-z0-9])[a-z]+[A-Z][A-Za-z0-9]*")  # iPhone, eBay, macOS
_CUE_NAME = re.compile(
    r"\b(?:such as|including|like|versus|vs\.?|instead of|rather than|alternatives? to"
    rf"|competitors? (?:like|such as|include|are))\s+({_WORD})",
    re.IGNORECASE,
)


def _words(block: str) -> frozenset[str]:
    return frozenset(re.split(r"\s+", block.strip()))


_COMMON = _words(
    """
    i in on at the a an and or of by for to from with it its this that these those my our we you
    they he she is are was were be been has have had do does did not no yes if as but so than then
    there here when where which who whom what why how all any both each few more most other some
    such only own same too very can will just also many much users user people students teams
    team companies company customers customer developers tools tool products product apps app
    services service competitors competitor over under about after before during between among
    founders founder freelancers owners managers engineers designers parents teachers learners
    consumers buyers sellers creators makers marketers businesses market markets demand pricing
    price prices competition complaints complaint reviews review feedback requests request
    problems problem solutions solution features feature plans plan costs cost fees fee growth
    trend trends adoption interest evidence data results result reports report studies study
    several every none one two three four five six seven eight nine ten first second third
    go pivot insufficient verdict score scores
    january february march april may june july august september october november december
    monday tuesday wednesday thursday friday saturday sunday
    """
)
_ACRONYMS = _words(
    """
    ai ml api ui ux cli pdf html css json sql url usd us uk eu cv id it b2b b2c mvp roi hn gpu
    cpu ram os pr saas sdk ide csv xml http https ok faq qa ci cd vs
    """
)
_GENERIC_AFTER_CUE = _COMMON | _words(
    """
    existing similar alternative alternatives incumbent incumbents rival rivals current popular
    major leading big small new old free paid open several various certain them one ones another
    others
    """
)


def _ordinary(word: str) -> bool:
    return word.lower() in _COMMON or word.lower() in _ACRONYMS


def _trim(words: list[str]) -> list[str]:
    while words and _ordinary(words[0]):
        words = words[1:]
    while words and _ordinary(words[-1]):
        words = words[:-1]
    return words


def extract_entities(text: str) -> list[str]:
    """Likely product or company names in ``text``, de-duplicated, in order of appearance."""
    found: list[tuple[int, str]] = []
    for match in _CAPITALISED.finditer(text):
        words = _trim(match.group(0).split(" "))
        phrase = " ".join(words)
        if words and len(phrase) >= 2:
            found.append((match.start(), phrase))
    found.extend((m.start(), m.group(0)) for m in _MIXED_CASE.finditer(text))
    for match in _CUE_NAME.finditer(text):
        word = match.group(1)
        if word.lower() not in _GENERIC_AFTER_CUE:
            found.append((match.start(1), word))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, name in sorted(found):
        if name.lower() not in seen:
            seen.add(name.lower())
            ordered.append(name)
    return ordered


def _contains(haystack: str, needle: str) -> bool:
    body = r"\s+".join(re.escape(p) for p in needle.split())
    return re.search(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", haystack, re.IGNORECASE) is not None


def find_unsupported_entities(
    claim: str, evidence_texts: Iterable[str], card_names: Iterable[str | None]
) -> list[str]:
    """Names in ``claim`` that appear in none of the evidence texts or card names."""
    texts = [t for t in evidence_texts if t]
    names = [n for n in card_names if n]
    return [
        e
        for e in extract_entities(claim)
        if not (any(_contains(c, e) for c in [*texts, *names]) or matches_any(e, names))
    ]
