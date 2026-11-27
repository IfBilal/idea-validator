"""Shared prompt-injection heuristics (docs/harness/13 §4 and §6).

One pattern list used by the M12 input rail and (later) the M5 sanitiser. Heuristics are a
defence-in-depth layer: they flag, they never rewrite. Text is normalised first (NFKC, case-fold,
invisible characters removed) so full-width letters and zero-width splits cannot hide a phrase.
Reported offsets refer to the *normalised* text.

All patterns are written to be linear-time: bounded repetition only, no nested quantifiers.
"""

import re
import unicodedata
from dataclasses import dataclass

_WS = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n+")


@dataclass(frozen=True, slots=True)
class InjectionHit:
    name: str
    category: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Pattern:
    name: str
    category: str
    regex: re.Pattern[str]


def _p(name: str, category: str, expr: str) -> _Pattern:
    return _Pattern(name, category, re.compile(expr))


# Up to three filler words between key words ("ignore all of the previous instructions").
_GAP = r"(?:\w+ ){0,3}"

PATTERNS: tuple[_Pattern, ...] = (
    _p(
        "ignore_previous",
        "instruction_override",
        rf"\b(?:ignore|disregard|forget|bypass|skip) {_GAP}"
        r"(?:previous|prior|above|earlier|preceding|former|your|my|these|those|all|any|the)"
        r" (?:\w+ ){0,2}"
        r"(?:instructions?|prompts?|rules?|guidelines?|directions?|context|messages?)\b",
    ),
    _p("forget_everything", "instruction_override", r"\bforget (?:everything|all)\b"),
    _p("new_instructions", "instruction_override", r"\bnew (?:system )?instructions?\s*:"),
    _p(
        "override_system",
        "instruction_override",
        rf"\boverride {_GAP}(?:system|safety|developer) (?:prompt|instructions?|rules?|policy)\b",
    ),
    _p("you_are_now", "role_play", r"\byou are now (?:an? |the )?\w+"),
    _p("pretend_to_be", "role_play", r"\bpretend (?:to be|you are|you're)\b"),
    _p("developer_mode", "role_play", r"\b(?:developer|debug|god|admin|sudo) mode\b"),
    _p("act_as_dan", "role_play", r"\bact as (?:dan|an? unrestricted|an? unfiltered)\b"),
    _p("jailbreak", "role_play", r"\bjail ?break(?:ing|s|ed)?\b"),
    _p(
        "reveal_prompt",
        "prompt_exfiltration",
        rf"\b(?:reveal|show|print|repeat|output|leak|display|tell me) {_GAP}"
        r"(?:system|hidden|initial|secret) (?:prompt|instructions?|message)\b",
    ),
    _p(
        "what_is_prompt",
        "prompt_exfiltration",
        r"\bwhat (?:is|are) your (?:system |initial |hidden )?(?:prompt|instructions)\b",
    ),
    _p("evidence_tag", "delimiter_break", r"</?\s*evidence\b"),
    _p("chatml_token", "delimiter_break", r"<\|[a-z_]{2,20}\|>"),
    _p("llama_inst", "delimiter_break", r"\[/?inst\]|<</?sys>>"),
    _p("system_tag", "delimiter_break", r"</?\s*(?:system|assistant|instructions?)\s*>"),
    _p("fenced_system", "delimiter_break", r"```\s*(?:system|assistant)\b"),
    _p("role_header", "fake_role_header", r"(?m)^[ ]{0,8}(?:system|assistant|developer)[ ]{0,3}:"),
    _p(
        "set_verdict",
        "verdict_manipulation",
        r"\b(?:set|return|output|give|declare|change|make) (?:\w+ ){0,3}"
        r"verdict (?:to |as |is |of |: ?)?(?:go|pivot|no-go|no go)\b",
    ),
    _p(
        "mark_verdict",
        "verdict_manipulation",
        r"\b(?:mark|rate|label|classify|score) (?:\w+ ){0,3}as (?:go|pivot|no-go|no go)\b",
    ),
)


def normalize_for_matching(text: str, *, keep_newlines: bool = False) -> str:
    """NFKC + case-fold, drop format characters, collapse whitespace, strip."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    visible = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    if keep_newlines:
        visible = visible.replace("\r\n", "\n").replace("\r", "\n")
        lines = (_WS.sub(" ", line).strip() for line in visible.split("\n"))
        return _BLANK_LINES.sub("\n", "\n".join(lines)).strip("\n")
    return " ".join(visible.split())


def find_injections(text: str) -> list[InjectionHit]:
    """All heuristic hits, in pattern order. Empty list means nothing suspicious was found."""
    if not text:
        return []
    flat = normalize_for_matching(text)
    lined = normalize_for_matching(text, keep_newlines=True)
    hits: list[InjectionHit] = []
    for pattern in PATTERNS:
        haystack = lined if pattern.category == "fake_role_header" else flat
        if (m := pattern.regex.search(haystack)) is not None:
            hits.append(InjectionHit(pattern.name, pattern.category, m.start(), m.end()))
    return hits


def contains_injection(text: str) -> bool:
    return bool(find_injections(text))


__all__ = [
    "PATTERNS",
    "InjectionHit",
    "contains_injection",
    "find_injections",
    "normalize_for_matching",
]
