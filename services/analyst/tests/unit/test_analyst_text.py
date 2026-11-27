"""Tokenising, TF-IDF cosine and Jaccard: pure, deterministic, no model download."""

import math

import pytest

from svc_analyst.services.text import (
    Embedder,
    TfidfEmbedder,
    centroid,
    cosine,
    jaccard,
    stem,
    tokenize,
)


def test_tokenize_lowercases_drops_stopwords_and_short_tokens() -> None:
    assert tokenize("The Invoices of a Freelancer, A!") == ["invoice", "freelancer"]


def test_tokenize_empty_and_punctuation_only() -> None:
    assert tokenize("") == []
    assert tokenize("  ... !!! ") == []


def test_tokenize_keeps_digits_and_is_stable() -> None:
    assert tokenize("GPT4 api v2") == ["gpt4", "api", "v2"]
    assert tokenize("GPT4 api v2") == tokenize("GPT4 api v2")


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("invoices", "invoice"),
        ("companies", "company"),
        ("class", "class"),  # ss kept
        ("bus", "bus"),  # us kept
        ("analysis", "analysis"),  # is kept
        ("is", "is"),
        ("cat", "cat"),
        ("ties", "tie"),  # too short for the ies rule (len 4): plain s rule
        ("cats", "cat"),
        ("apps", "app"),
    ],
)
def test_stem_cases(word: str, expected: str) -> None:
    assert stem(word) == expected


def test_cosine_identical_orthogonal_and_empty() -> None:
    a = {"x": 1.0, "y": 2.0}
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine({"x": 1.0}, {"y": 1.0}) == 0.0
    assert cosine({}, a) == 0.0
    assert cosine(a, {}) == 0.0
    assert cosine({}, {}) == 0.0


def test_cosine_is_symmetric_exactly() -> None:
    a = {"x": 0.1, "y": 0.7, "z": 0.3}
    b = {"y": 0.2, "z": 0.9, "w": 0.5}
    assert cosine(a, b) == cosine(b, a)


def test_cosine_known_value() -> None:
    assert cosine({"a": 1.0, "b": 1.0}, {"a": 1.0}) == pytest.approx(1 / math.sqrt(2))


def test_jaccard_cases() -> None:
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_centroid_is_normalised_and_deterministic() -> None:
    c = centroid([{"a": 1.0}, {"b": 1.0}])
    assert cosine(c, c) == pytest.approx(1.0)
    assert c["a"] == pytest.approx(c["b"])
    assert centroid([]) == {}


def test_tfidf_similar_texts_score_higher_than_unrelated() -> None:
    corpus = [
        "invoice tracking for freelancers",
        "freelancer invoice reminders",
        "gpu cluster scheduler",
        "kubernetes gpu scheduling",
    ]
    emb = TfidfEmbedder(corpus)
    v = emb.vectorize(corpus)
    assert cosine(v[0], v[1]) > cosine(v[0], v[2])
    assert cosine(v[2], v[3]) > cosine(v[1], v[2])


def test_tfidf_vectors_are_unit_length_and_empty_text_is_empty_vector() -> None:
    emb = TfidfEmbedder(["alpha beta", "beta gamma"])
    v = emb.vectorize(["alpha beta", "the of"])
    assert cosine(v[0], v[0]) == pytest.approx(1.0)
    assert v[1] == {}


def test_tfidf_unseen_terms_get_max_idf_and_do_not_crash() -> None:
    emb = TfidfEmbedder(["alpha beta"])
    (v,) = emb.vectorize(["zzzz alpha"])
    assert v["zzzz"] > v["alpha"]


def test_tfidf_empty_corpus_is_usable() -> None:
    (v,) = TfidfEmbedder([]).vectorize(["alpha"])
    assert v == {"alpha": pytest.approx(1.0)}


def test_tfidf_is_deterministic_and_order_independent_of_corpus() -> None:
    corpus = ["a1 b1 c1", "b1 c1 d1", "e1 f1"]
    one = TfidfEmbedder(corpus).vectorize(["b1 e1"])
    two = TfidfEmbedder(list(reversed(corpus))).vectorize(["b1 e1"])
    assert one == two


def test_tfidf_satisfies_the_embedder_protocol() -> None:
    emb: Embedder = TfidfEmbedder(["x"])
    assert emb.vectorize([]) == []


def test_cosine_of_zero_weight_vectors_is_zero() -> None:
    assert cosine({"a": 0.0}, {"a": 1.0}) == 0.0


def test_cosine_at_a_threshold_is_stable_after_rounding() -> None:
    # 0.8 exactly: the service rounds to 6 places before comparing with the 0.80 threshold
    assert round(cosine({"x": 0.8, "y": 0.6}, {"x": 1.0}), 6) == 0.8
