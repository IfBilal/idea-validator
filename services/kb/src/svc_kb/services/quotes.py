"""Pick the sentence of a chunk that best answers a query: a verbatim quote of at most 300 chars.

M12 (the critic) accepts a claim only if its quote is a substring of the cited chunk, so a quote
is never paraphrased and never gets an ellipsis: a long sentence is cut at a word boundary and
stays a substring. The chunk text and the quote go through the same ``normalise`` (chunking.py),
which is what makes ``quote in chunk`` true by construction.
"""

import re

from .chunking import normalise

MAX_QUOTE_CHARS = 300
# a sentence ends at . ! ? followed by whitespace and a capital, digit or opening quote/bracket:
# "3.5 dollars" and "e.g. this" do not split
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_TERM = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is"),
        *("it", "of", "on", "or", "that", "the", "this", "to", "was", "were", "what", "when"),
        *("where", "which", "with"),
    }
)


def _terms(text: str) -> set[str]:
    return {t for t in _TERM.findall(text.lower()) if t not in _STOP_WORDS}


def _cut(sentence: str, limit: int) -> str:
    if len(sentence) <= limit:
        return sentence
    head = sentence[:limit]
    if sentence[limit] == " ":  # the cut already falls between two words
        return head
    if " " in head:
        return head.rsplit(" ", 1)[0]
    return head  # one endless word: a hard cut is all there is


def best_quote(text: str, query: str, *, max_chars: int = MAX_QUOTE_CHARS) -> str:
    """The sentence with the most distinct query terms (earliest on a tie), cut to ``max_chars``."""
    clean = normalise(text)
    if not clean:
        return ""
    sentences = _SENTENCE_BREAK.split(clean)
    wanted = _terms(query)
    best = max(range(len(sentences)), key=lambda i: (len(wanted & _terms(sentences[i])), -i))
    return _cut(sentences[best], max_chars)
