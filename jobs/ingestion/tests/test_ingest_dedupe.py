import random
from datetime import date
from typing import Any

import pytest

from ingestion.dedupe import DedupeConfig, dedupe, shingles
from ingestion.mappers import map_item
from ingestion.minhash import MinHasher, jaccard
from ingestion.models import RawItem

TODAY = date(2026, 3, 1)


def draft(url: str, title: str, text: str = "", source: str = "hackernews", **kw: Any) -> Any:
    item = RawItem.model_validate({"source": source, "url": url, "title": title, "text": text} | kw)
    return map_item(item, fetched_on=TODAY)


# ---- minhash ----------------------------------------------------------------------------------


def test_jaccard_edges() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0  # two empty sets are not "the same card"
    assert jaccard(frozenset({"a"}), frozenset({"a"})) == 1.0
    assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)


def test_signatures_are_deterministic_for_one_seed_and_differ_between_seeds() -> None:
    a = MinHasher(num_perm=64, seed=7)
    b = MinHasher(num_perm=64, seed=7)
    c = MinHasher(num_perm=64, seed=8)
    items = frozenset({"alpha beta", "beta gamma", "gamma delta"})
    assert a.signature(items) == b.signature(items)
    assert a.signature(items) != c.signature(items)


def test_signature_estimates_jaccard() -> None:
    hasher = MinHasher(num_perm=256, seed=1)
    left = frozenset(f"s{i}" for i in range(100))
    right = frozenset(f"s{i}" for i in range(20, 120))  # true jaccard 80/120
    sa, sb = hasher.signature(left), hasher.signature(right)
    estimate = sum(x == y for x, y in zip(sa, sb, strict=True)) / 256
    assert abs(estimate - 80 / 120) < 0.1


def test_a_signature_of_an_empty_set_is_defined() -> None:
    assert len(MinHasher(num_perm=16, seed=1).signature(frozenset())) == 16


def test_bad_parameters_are_refused() -> None:
    with pytest.raises(ValueError):
        MinHasher(num_perm=0, seed=1)
    with pytest.raises(ValueError):
        DedupeConfig(threshold=0.0)
    with pytest.raises(ValueError):
        DedupeConfig(threshold=1.5)
    with pytest.raises(ValueError):
        DedupeConfig(num_perm=10, bands=3)  # bands must divide num_perm


def test_shingles_are_unicode_aware_and_ignore_punctuation_and_case() -> None:
    assert shingles("Café, CAFÉ!") == shingles("café café")
    assert shingles("") == frozenset()
    assert shingles("!!! ???") == frozenset()
    assert len(shingles("one")) == 1  # a single word is still comparable


# ---- exact and near duplicates ----------------------------------------------------------------


def test_distinct_cards_all_survive() -> None:
    plan = dedupe(
        [
            draft("https://a.com/1", "Invoicing tool for freelancers", "Send invoices fast."),
            draft("https://b.com/2", "Recipe sharing app for families", "Share meals."),
            draft("https://c.com/3", "Open source CRM written in Rust", "Fast crm."),
        ],
        DedupeConfig(),
    )
    assert [p.card.url for p in plan.cards] == [
        "https://a.com/1",
        "https://b.com/2",
        "https://c.com/3",
    ]
    assert plan.duplicates == 0


def test_the_same_url_twice_is_one_card_with_both_platforms() -> None:
    plan = dedupe(
        [
            draft("https://github.com/o/r", "o/r", "A tool.", source="github"),
            draft(
                "https://www.github.com/o/r/?utm_source=hn",
                "Show HN: R",
                "A tool.",
                source="hackernews",
            ),
        ],
        DedupeConfig(),
    )
    (only,) = plan.cards
    assert set(only.platforms) == {"github", "hackernews"}
    assert only.merged == 1
    assert plan.duplicates == 1


def test_the_same_text_at_two_urls_is_an_exact_duplicate() -> None:
    plan = dedupe(
        [
            draft("https://a.com/1", "Same", "Identical body text here."),
            draft("https://b.com/2", "Same", "Identical body text here.", source="reddit"),
        ],
        DedupeConfig(),
    )
    (only,) = plan.cards
    assert set(only.platforms) == {"hackernews", "reddit"}


def test_a_near_duplicate_is_merged_and_records_the_other_platform() -> None:
    plan = dedupe(
        [
            draft(
                "https://a.com/x",
                "Pocket invoices: invoicing for freelancers and small teams",
                "Create send and track invoices in seconds.",
            ),
            draft(
                "https://producthunt.com/posts/pocket-invoices",
                "Pocket invoices: invoicing for freelancers and small teams!",
                "Create send and track invoices in seconds.",
                source="producthunt",
            ),
        ],
        DedupeConfig(threshold=0.8),
    )
    (only,) = plan.cards
    assert set(only.platforms) == {"hackernews", "producthunt"}


def test_unrelated_cards_are_not_merged_even_when_the_threshold_is_low() -> None:
    plan = dedupe(
        [
            draft("https://a.com/1", "Invoicing tool for freelancers", "Send invoices fast."),
            draft("https://b.com/2", "Recipe sharing app for families", "Share family meals."),
        ],
        DedupeConfig(threshold=0.3),
    )
    assert len(plan.cards) == 2


def _pair(shared: int, own: int) -> tuple[Any, Any]:
    """Two cards whose name+summary shingle sets share exactly ``shared`` of ``shared+2*own``."""
    base = " ".join(f"w{i}" for i in range(shared + 1))  # shared+1 words -> shared bigrams
    a = base + " " + " ".join(f"a{i}" for i in range(own))
    b = base + " " + " ".join(f"b{i}" for i in range(own))
    return (
        draft("https://a.com/1", a, "", source="hackernews"),
        draft("https://b.com/2", b, "", source="reddit"),
    )


def test_the_threshold_is_inclusive() -> None:
    left, right = _pair(9, 1)
    exact = jaccard(
        shingles(left.name + " " + left.summary), shingles(right.name + " " + right.summary)
    )
    assert 0.0 < exact < 1.0
    at = dedupe([left, right], DedupeConfig(threshold=exact))
    above = dedupe([left, right], DedupeConfig(threshold=min(1.0, exact + 0.02)))
    assert len(at.cards) == 1, "similarity equal to the threshold merges"
    assert len(above.cards) == 2, "similarity below the threshold does not"


def test_result_does_not_depend_on_input_order() -> None:
    cards = [
        draft(f"https://a.com/{i}", f"Distinct product number {i} about topic {i * 7}", f"Body {i}")
        for i in range(30)
    ]
    cards += [draft("https://x.com/dup", cards[3].name, "Body 3", source="reddit")]
    forward = dedupe(cards, DedupeConfig())
    shuffled = list(cards)
    random.Random(5).shuffle(shuffled)  # noqa: S311
    other = dedupe(shuffled, DedupeConfig())
    assert [p.card.url for p in forward.cards] == [p.card.url for p in other.cards]
    assert [p.platforms for p in forward.cards] == [p.platforms for p in other.cards]


def test_dedupe_is_idempotent_on_its_own_output() -> None:
    cards = [
        draft("https://a.com/1", "Same thing", "Same body"),
        draft("https://b.com/2", "Same thing", "Same body", source="reddit"),
        draft("https://c.com/3", "Other thing entirely", "Different body"),
    ]
    once = dedupe(cards, DedupeConfig())
    again = dedupe([p.card for p in once.cards], DedupeConfig())
    assert [p.card for p in again.cards] == [p.card for p in once.cards]
    assert again.duplicates == 0


def test_the_earliest_post_is_the_representative() -> None:
    plan = dedupe(
        [
            draft("https://b.com/2", "Same", "Body text", source="reddit", created_at="2026-02-10"),
            draft("https://a.com/1", "Same", "Body text", created_at="2026-01-05"),
        ],
        DedupeConfig(),
    )
    assert plan.cards[0].card.url == "https://a.com/1"


def test_empty_input_gives_an_empty_plan() -> None:
    plan = dedupe([], DedupeConfig())
    assert plan.cards == [] and plan.duplicates == 0


def test_a_chain_of_near_duplicates_collapses_to_one() -> None:
    words = " ".join(f"token{i}" for i in range(40))
    cards = [draft(f"https://s{i}.com/p", words + f" extra{i}", "") for i in range(5)]
    assert len(dedupe(cards, DedupeConfig(threshold=0.9)).cards) == 1


def test_many_cards_stay_fast() -> None:
    cards = [
        draft(f"https://a.com/{i}", f"unique product {i} for vertical {i % 97}", f"detail {i} " * 5)
        for i in range(3000)
    ]
    plan = dedupe(cards, DedupeConfig())
    assert len(plan.cards) == 3000
