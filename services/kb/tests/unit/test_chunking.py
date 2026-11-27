"""Chunking: card_id on every chunk, bounded size, overlap, deterministic (docs/harness/10 and 04 section 2)."""

import itertools
import re
import time

import pytest

from common.ids import parse_evidence_id
from svc_kb.services.chunking import MAX_CHUNKS, Chunk, chunk_text

CARD = "card_c4ed1c21"


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_a_short_text_is_one_chunk_with_the_first_evidence_id() -> None:
    chunks = chunk_text(CARD, "A slow export  in\nnote apps.")
    assert chunks == [
        Chunk(evidence_id=f"{CARD}#c0", card_id=CARD, index=0, text="A slow export in note apps.")
    ]


@pytest.mark.parametrize("empty", ["", "   ", "\n\t \n"])
def test_empty_text_gives_no_chunks(empty: str) -> None:
    assert chunk_text(CARD, empty) == []


def test_every_chunk_carries_the_card_id_and_a_valid_sequential_evidence_id() -> None:
    chunks = chunk_text(CARD, words(1000), max_words=100, overlap_words=10)
    assert len(chunks) > 5
    for position, chunk in enumerate(chunks):
        assert chunk.card_id == CARD and chunk.index == position
        assert parse_evidence_id(chunk.evidence_id) == (CARD, position)


def test_chunks_are_bounded_and_overlap_exactly_as_asked() -> None:
    text = words(400)
    chunks = chunk_text(CARD, text, max_words=100, overlap_words=20)
    parts = [c.text.split() for c in chunks]
    assert all(len(p) <= 100 for p in parts)
    for before, after in itertools.pairwise(parts):
        assert before[-20:] == after[:20], "consecutive chunks must share the overlap"


def test_no_word_is_lost_and_none_is_invented() -> None:
    text = words(777)
    chunks = chunk_text(CARD, text, max_words=100, overlap_words=15)
    seen: list[str] = []
    for c in chunks:
        parts = c.text.split()
        seen.extend(parts if not seen else parts[15:])
    assert seen == text.split()


def test_a_tail_made_only_of_overlap_does_not_become_its_own_chunk() -> None:
    # 100 + (100 - 20) = 180 words end exactly on a window boundary: no trailing 20-word duplicate chunk
    chunks = chunk_text(CARD, words(180), max_words=100, overlap_words=20)
    assert [len(c.text.split()) for c in chunks] == [100, 100]


def test_chunking_is_deterministic() -> None:
    text = words(500)
    assert chunk_text(CARD, text) == chunk_text(CARD, text)


def test_default_windows_match_the_spec_of_about_512_tokens_with_64_overlap() -> None:
    chunks = chunk_text(CARD, words(2000))
    sizes = [len(c.text.split()) for c in chunks]
    assert max(sizes) <= 380, "380 words stays under 512 tokens for ordinary English"
    assert sizes[0] >= 300


@pytest.mark.parametrize(("max_words", "overlap"), [(0, 0), (10, 10), (10, 11), (10, -1), (-5, 0)])
def test_nonsense_window_settings_are_refused(max_words: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="window"):
        chunk_text(CARD, "a b c", max_words=max_words, overlap_words=overlap)


def test_a_bad_card_id_is_refused_before_any_work() -> None:
    with pytest.raises(ValueError, match="card id"):
        chunk_text("not-a-card", "text")


def test_a_hostile_huge_text_is_capped_and_fast() -> None:
    started = time.perf_counter()
    chunks = chunk_text(CARD, "word " * 2_000_000)
    assert len(chunks) == MAX_CHUNKS
    assert time.perf_counter() - started < 5.0


def test_unicode_and_odd_whitespace_survive_and_are_normalised() -> None:
    e_acute, nbsp, i_diaeresis, em_space = chr(0xE9), chr(0xA0), chr(0xEF), chr(0x2003)
    raw = f"caf{e_acute}{nbsp}na{i_diaeresis}ve{em_space}plan\r\nnext"  # code points, not literals
    chunks = chunk_text(CARD, raw)
    assert len(chunks) == 1
    assert re.fullmatch(r"\S+( \S+)*", chunks[0].text), "every odd space became one plain space"
    assert f"caf{e_acute}" in chunks[0].text


def test_control_characters_are_removed_from_chunk_text() -> None:
    chunks = chunk_text(CARD, "alpha\x00beta \x07gamma\x7f delta")
    assert chunks[0].text == "alphabeta gamma delta"
