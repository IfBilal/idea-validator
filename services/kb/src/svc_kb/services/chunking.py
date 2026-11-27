"""Split a card's text into overlapping chunks that each keep their card id (Guide section 12).

A chunk is the unit that is embedded, retrieved and cited, so its id is the evidence id the
verdict engine will later point at: ``card_<8 hex>#c<n>``. Windows are counted in words as a
stand-in for the spec's "512 tokens with 64 overlap": 350 words stays under 512 tokens for
ordinary English, and 45 words of overlap is about 64 tokens. No tokenizer is needed, which keeps
chunking pure and fast.
"""

import re
from dataclasses import dataclass

from common.ids import evidence_id

MAX_WORDS = 350
OVERLAP_WORDS = 45
# per card: about 19,000 words, far beyond any scraped post; a hostile body cannot flood the index
MAX_CHUNKS = 64
MAX_INPUT_CHARS = 400_000  # cut before splitting, so a huge body costs almost nothing


# control characters that are not whitespace; the rest (tab, newline, ...) separate words anyway
_CONTROLS = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")


def normalise(text: str) -> str:
    """Control characters out, every run of Unicode whitespace becomes one space.

    Chunk text and quotes both go through this, so a quote is a substring of its chunk.
    """
    return " ".join(_CONTROLS.sub("", text).split())


@dataclass(frozen=True)
class Chunk:
    evidence_id: str
    card_id: str
    index: int
    text: str


def chunk_text(
    card_id: str,
    text: str,
    *,
    max_words: int = MAX_WORDS,
    overlap_words: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """Overlapping word windows with sequential evidence ids; ``[]`` for empty text."""
    evidence_id(card_id, 0)  # refuses a malformed card id before any work
    if max_words < 1 or overlap_words < 0 or overlap_words >= max_words:
        raise ValueError(f"bad window: max_words={max_words}, overlap_words={overlap_words}")
    words = normalise(text[:MAX_INPUT_CHARS]).split()
    chunks: list[Chunk] = []
    stride = max_words - overlap_words
    covered = 0
    start = 0
    while start < len(words) and len(chunks) < MAX_CHUNKS:
        end = min(start + max_words, len(words))
        if chunks and end <= covered:  # the window would only repeat the previous overlap
            break
        index = len(chunks)
        chunks.append(
            Chunk(evidence_id(card_id, index), card_id, index, " ".join(words[start:end]))
        )
        covered = end
        start += stride
    return chunks
