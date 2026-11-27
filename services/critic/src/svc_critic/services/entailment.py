"""Support judging: does the cited evidence back the claim?

The judge only says supported / unsupported and may propose a verbatim quote. It never rewrites a
claim and never produces a verdict. The default judge is deterministic (lexical overlap). An LLM
judge (M5 ``/llm``, prompt ``critic@v1``) can implement the same Protocol later; the checker
re-verifies any quote it returns, so a model cannot invent one.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..models.evidence import EvidenceRecord

_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_STOPWORDS = frozenset(
    "the and for are was were with that this from have has had not but you your can will "  # noqa: SIM905
    "would should could they them their there then than into onto over under about also just "
    "very more most some any all its our out who whom what when where which while why how too "
    "own same such been being does did doing both each few other only so is it of to in on at "
    "by as an or if be do no up we he she his her him".split()
)
REASON_OK = "ok"
REASON_NO_SUPPORT = "quote does not support"
REASON_EMPTY_CLAIM = "claim has no checkable content"


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    supported: bool
    quote: str | None
    reason: str


class EntailmentJudge(Protocol):
    async def judge(self, claim: str, evidence: Sequence[EvidenceRecord]) -> JudgeVerdict: ...


def _stem(word: str) -> str:
    word = word.strip("'-")
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_words(text: str) -> set[str]:
    """Lower-cased, lightly de-pluralised words of length >= 3 that are not stopwords."""
    words = (_stem(w) for w in _WORD.findall(text.lower()))
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


class LexicalOverlapJudge:
    """Supported iff one sentence contains >= ``threshold`` of the claim's content words."""

    def __init__(self, threshold: float = 0.5, max_quote_chars: int = 200) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if max_quote_chars < 1:
            raise ValueError("max_quote_chars must be positive")
        self._threshold = threshold
        self._max_quote = max_quote_chars

    async def judge(self, claim: str, evidence: Sequence[EvidenceRecord]) -> JudgeVerdict:
        wanted = content_words(claim)
        if not wanted:
            return JudgeVerdict(False, None, REASON_EMPTY_CLAIM)
        best_score, best_sentence = 0.0, ""
        for record in evidence:
            for raw in _SENTENCE_SPLIT.split(record.body):
                sentence = raw.strip()
                if not sentence:
                    continue
                score = len(wanted & content_words(sentence)) / len(wanted)
                if score > best_score:
                    best_score, best_sentence = score, sentence
        if best_score > 0.0 and best_score >= self._threshold:
            return JudgeVerdict(True, best_sentence[: self._max_quote], REASON_OK)
        return JudgeVerdict(False, None, REASON_NO_SUPPORT)
