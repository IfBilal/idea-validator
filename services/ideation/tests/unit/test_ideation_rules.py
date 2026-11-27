import pytest
from hypothesis import given
from hypothesis import strategies as st
from ideation_fakes import profile

from svc_ideation.services.fit import EFFORT_HOURS, available_hours, fits_profile
from svc_ideation.services.similarity import feature_overlap, jaccard, tokenize


def test_effort_hours_match_the_spec():
    assert EFFORT_HOURS == {"S": 60, "M": 160, "L": 400}


@pytest.mark.parametrize(
    ("hours", "weeks", "team", "expected"),
    [(10, 10, 1, 100), (0, 10, 1, 0), (80, 52, 10, 41600), (5, 8, 2, 80), (1, 1, 1, 1)],
)
def test_available_hours_is_the_product(hours: int, weeks: int, team: int, expected: int):
    assert available_hours(profile(hours, weeks, team)) == expected


@pytest.mark.parametrize(
    ("scope", "hours", "weeks", "team", "fits"),
    [
        ("S", 6, 10, 1, True),  # 60 == 60: boundary is inclusive
        ("S", 59, 1, 1, False),  # 59 < 60
        ("S", 60, 1, 1, True),
        ("M", 16, 10, 1, True),  # 160 == 160
        ("M", 53, 3, 1, False),  # 159
        ("L", 10, 10, 4, True),  # 400 == 400 with a team of four
        ("L", 10, 10, 3, False),  # 300 < 400
        ("S", 0, 10, 1, False),  # no hours at all fits nothing
        ("L", 80, 52, 10, True),
    ],
)
def test_fits_profile_boundaries(scope: str, hours: int, weeks: int, team: int, fits: bool):
    assert fits_profile(scope, profile(hours, weeks, team)) is fits  # type: ignore[arg-type]


@pytest.mark.parametrize("scope", ["XL", "", "s", None])
def test_unknown_scope_never_fits(scope: object):
    assert fits_profile(scope, profile(80, 52, 10)) is False  # type: ignore[arg-type]


def test_tokenize_lowercases_and_drops_stopwords_and_short_words():
    assert tokenize("The Exam-Countdown for AI of 2026!") == frozenset(
        {"exam", "countdown", "2026"}
    )


def test_tokenize_empty_and_punctuation_only():
    assert tokenize("") == frozenset()
    assert tokenize("?! ... --") == frozenset()


def test_jaccard_basic_values():
    assert jaccard(frozenset("abc"), frozenset("abc")) == 1.0
    assert jaccard(frozenset("abc"), frozenset("def")) == 0.0
    assert jaccard(frozenset("abc"), frozenset("abd")) == 0.5
    assert jaccard(frozenset(), frozenset("abc")) == 0.0
    assert jaccard(frozenset(), frozenset()) == 0.0


@given(
    st.frozensets(st.text(min_size=1, max_size=4)), st.frozensets(st.text(min_size=1, max_size=4))
)
def test_jaccard_is_symmetric_and_bounded(a: frozenset[str], b: frozenset[str]):
    assert jaccard(a, b) == jaccard(b, a)
    assert 0.0 <= jaccard(a, b) <= 1.0


def test_feature_overlap_compares_all_features_with_the_product_text():
    score = feature_overlap(
        ["exam countdown", "spaced repetition"], "NoteApp: exam countdown, spaced repetition"
    )
    assert score == pytest.approx(4 / 5)  # 4 shared tokens, plus "noteapp" only in the product


def test_feature_overlap_of_unrelated_text_is_zero():
    assert feature_overlap(["exam countdown"], "kubernetes operator for postgres") == 0.0
