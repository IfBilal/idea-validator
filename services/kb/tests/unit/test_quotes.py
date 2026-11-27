"""Sentence-level quotes (docs/harness/10 section 6): at most 300 characters, and always verbatim.

M12 (critic) accepts a claim only if its quote is a substring of the cited chunk, so a quote must never
be paraphrased, reflowed or given an ellipsis: every case below also asserts ``quote in chunk``.
"""

import pytest

from svc_kb.services.quotes import MAX_QUOTE_CHARS, best_quote

TEXT = (
    "We launched last year. Exporting notes is painfully slow when the vault passes 5,000 files. "
    "The team ignored the export bug for months. Pricing is fair."
)


def test_the_sentence_that_covers_most_query_terms_wins() -> None:
    quote = best_quote(TEXT, "slow export of notes")
    assert quote == "Exporting notes is painfully slow when the vault passes 5,000 files."
    assert quote in TEXT


def test_a_tie_goes_to_the_earliest_sentence() -> None:
    assert best_quote("Alpha beta here. Alpha beta there.", "alpha beta") == "Alpha beta here."


def test_with_no_overlap_the_first_sentence_is_the_quote() -> None:
    assert best_quote(TEXT, "quantum chromodynamics") == "We launched last year."


def test_matching_ignores_case_punctuation_and_stop_words() -> None:
    assert (
        best_quote(TEXT, "THE Slow, EXPORT!!")
        == "Exporting notes is painfully slow when the vault passes 5,000 files."
    )


def test_a_long_sentence_is_cut_at_a_word_boundary_without_an_ellipsis() -> None:
    long_sentence = " ".join(f"word{i}" for i in range(200)) + "."
    quote = best_quote(long_sentence, "word5")
    assert 0 < len(quote) <= MAX_QUOTE_CHARS
    assert quote in long_sentence, "a truncated quote must still be verbatim"
    assert not quote.endswith("…") and not quote.endswith("...")
    assert long_sentence[len(quote)] == " ", "the cut must fall on a word boundary"


def test_a_sentence_of_exactly_the_limit_is_kept_whole() -> None:
    sentence = ("x" * (MAX_QUOTE_CHARS - 1)) + "."
    assert best_quote(sentence, "x") == sentence


def test_one_endless_word_is_hard_cut_to_the_limit() -> None:
    quote = best_quote("y" * 10_000, "y")
    assert len(quote) == MAX_QUOTE_CHARS
    assert quote in "y" * 10_000


@pytest.mark.parametrize("empty", ["", "   ", "\n\n", "\t"])
def test_nothing_to_quote_gives_an_empty_string(empty: str) -> None:
    assert best_quote(empty, "anything") == ""


def test_an_empty_query_still_returns_the_first_sentence() -> None:
    assert best_quote(TEXT, "") == "We launched last year."


def test_whitespace_and_control_characters_are_normalised_before_choosing() -> None:
    chunk = "First   line.\x00\x07 Second\nline has the term needle."
    quote = best_quote(chunk, "needle")
    assert "\x00" not in quote and "\x07" not in quote
    assert quote == "Second line has the term needle."


def test_a_custom_limit_is_honoured() -> None:
    quote = best_quote("alpha beta gamma delta epsilon", "alpha", max_chars=12)
    assert quote == "alpha beta"
    assert len(quote) <= 12


def test_results_are_deterministic() -> None:
    assert best_quote(TEXT, "export slow") == best_quote(TEXT, "export slow")


def test_abbreviation_dots_do_not_split_a_sentence_into_a_useless_fragment() -> None:
    chunk = "Costs about 3.5 dollars per user. Not cheap."
    assert best_quote(chunk, "dollars per user") == "Costs about 3.5 dollars per user."


def test_the_quote_of_every_chunk_is_a_substring_of_that_chunk() -> None:
    """The invariant M12 relies on, checked on real chunks (control characters, odd spaces, long text)."""
    from svc_kb.services.chunking import chunk_text

    body = " ".join(
        f"Sentence {i} about slow export \x00 and note vaults, with detail number {i}."
        for i in range(400)
    )
    chunks = chunk_text("card_c4ed1c21", body)
    assert len(chunks) > 3
    for chunk in chunks:
        for query in ("slow export", "vault detail 17", "", "zzz unmatched"):
            quote = best_quote(chunk.text, query)
            assert quote and len(quote) <= MAX_QUOTE_CHARS
            assert quote in chunk.text, (chunk.evidence_id, query)
