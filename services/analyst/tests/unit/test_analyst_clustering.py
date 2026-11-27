"""Clustering, c-TF-IDF names, monthly stats, slope and status."""

import random
import time
from datetime import date

import pytest

from svc_analyst.fixtures import build_cards, outlier_cards, synthetic_cards
from svc_analyst.models.cards import Card
from svc_analyst.services.clustering import (
    ClusterConfig,
    agglomerate,
    analyse,
    classify,
    least_squares_slope,
    monthly_series,
    niche_ids,
    normalised_slope,
    window_months,
)


@pytest.fixture
def cards() -> list[Card]:
    return build_cards()


@pytest.fixture
def cards_with_outliers() -> list[Card]:
    return sorted([*build_cards(), *outlier_cards()], key=lambda c: c.id)


def card(i: int, text: str, **kw: object) -> Card:
    base: dict[str, object] = {
        "id": f"card_{i:08x}",
        "name": "x",
        "text": text,
        "source": "reddit",
        "kind": "product",
        "published_at": date(2026, 6, 1),
    }
    base.update(kw)
    return Card.model_validate(base)


# ---- slope -----------------------------------------------------------------------------------


def test_least_squares_slope_known_series() -> None:
    assert least_squares_slope([0, 1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert least_squares_slope([5, 4, 3, 2, 1, 0]) == pytest.approx(-1.0)
    assert least_squares_slope([2, 2, 2, 2, 2, 2]) == 0.0
    assert least_squares_slope([0, 0, 0, 0, 0, 6]) == pytest.approx(6 * 2.5 / 17.5)


def test_least_squares_slope_degenerate_lengths() -> None:
    assert least_squares_slope([]) == 0.0
    assert least_squares_slope([7]) == 0.0
    assert least_squares_slope([1, 3]) == pytest.approx(2.0)


def test_normalised_slope_divides_by_the_mean() -> None:
    assert normalised_slope([1, 2, 3, 4, 5, 6]) == pytest.approx(1 / 3.5)
    assert normalised_slope([0, 0, 0, 0, 0, 0]) == 0.0  # mean 0: no trend, no crash
    assert normalised_slope([10, 10, 10, 10, 10, 10]) == 0.0
    assert normalised_slope([6, 5, 4, 3, 2, 1]) < 0


# ---- window / series -------------------------------------------------------------------------


def test_window_months_is_six_consecutive_first_of_month_dates() -> None:
    assert window_months(date(2026, 2, 17), 6) == [
        date(2025, 9, 1),
        date(2025, 10, 1),
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert window_months(date(2026, 6, 30), 1) == [date(2026, 6, 1)]


def test_monthly_series_counts_launches_and_mentions_and_fills_gaps() -> None:
    cards = [
        card(1, "a", kind="product", published_at=date(2026, 1, 5)),
        card(2, "a", kind="repo", published_at=date(2026, 1, 9)),  # repo is not a launch
        card(3, "a", kind="complaint", published_at=date(2026, 3, 1)),
        card(4, "a", kind="request", published_at=date(2026, 3, 31)),
        card(5, "a", kind="discussion", published_at=date(2026, 3, 31)),
        card(6, "a", kind=None, published_at=date(2026, 3, 31)),  # unknown kind: a mention
        card(7, "a", kind="product", published_at=None),  # undated: ignored
    ]
    series = monthly_series(cards, as_of=date(2026, 4, 10))
    assert [(s.month, s.launches, s.mentions) for s in series] == [
        (date(2026, 1, 1), 1, 0),
        (date(2026, 2, 1), 0, 0),
        (date(2026, 3, 1), 0, 4),
        (date(2026, 4, 1), 0, 0),
    ]


def test_monthly_series_ignores_cards_after_as_of_and_handles_empty() -> None:
    late = card(1, "a", published_at=date(2026, 8, 1))
    assert monthly_series([late], as_of=date(2026, 6, 1)) == []
    assert monthly_series([], as_of=date(2026, 6, 1)) == []


# ---- classify (status thresholds) ----------------------------------------------------------------

CFG = ClusterConfig()


def status(
    slope: float,
    size: int = 5,
    sizes: tuple[int, ...] = (5, 1, 2, 3),
    launches: int = 0,
    mentions: int = 1,
) -> str:
    return classify(slope, size, list(sizes), launches, mentions, CFG)


def test_rising_threshold_is_inclusive_at_010() -> None:
    assert status(0.10) == "rising"
    assert status(0.0999) == "flat"
    assert status(0.5) == "rising"


def test_rising_beats_saturated() -> None:
    assert status(0.5, launches=10, mentions=1) == "rising"


def test_saturated_requires_flat_slope_top_quartile_and_launch_ratio() -> None:
    assert (
        status(0.02, launches=3, mentions=2) == "saturated"
    )  # ratio 1.5, slope 0.02: both inclusive
    assert status(0.0201, launches=3, mentions=2) == "flat"  # slope just above
    assert status(0.0, launches=29, mentions=20) == "flat"  # ratio 1.45
    assert status(-0.4, launches=3, mentions=2) == "saturated"  # falling and crowded
    assert status(0.0, size=3, launches=3, mentions=2) == "flat"  # not in the top quartile


def test_saturated_with_no_mentions_but_launches_counts_as_infinite_ratio() -> None:
    assert status(0.0, launches=1, mentions=0) == "saturated"
    assert status(0.0, launches=0, mentions=0) == "flat"


def test_top_quartile_boundaries() -> None:
    sizes = list(range(1, 9))  # 8 niches: the top quartile is the two largest
    assert classify(0.0, 7, sizes, 3, 1, CFG) == "saturated"
    assert classify(0.0, 6, sizes, 3, 1, CFG) == "flat"
    assert classify(0.0, 4, [4], 3, 1, CFG) == "saturated"  # a lone niche is its own top quartile
    assert classify(0.0, 5, [5, 5, 5, 5], 3, 1, CFG) == "saturated"  # ties all qualify


# ---- agglomerate -----------------------------------------------------------------------------


def test_agglomerate_empty_single_and_orthogonal() -> None:
    assert agglomerate([], 0.2) == []
    assert agglomerate([{"a": 1.0}], 0.2) == [[0]]
    assert agglomerate([{"a": 1.0}, {"b": 1.0}], 0.2) == [[0], [1]]


def test_agglomerate_merges_similar_vectors_at_or_above_threshold() -> None:
    a, b = {"x": 1.0}, {"x": 0.6, "y": 0.8}  # cosine 0.6
    assert agglomerate([a, b], 0.6) == [[0, 1]]  # inclusive
    assert agglomerate([a, b], 0.61) == [[0], [1]]


def test_agglomerate_groups_obvious_topics() -> None:
    vectors = [{"a": 1.0}, {"b": 1.0}, {"a": 0.9, "c": 0.1}, {"b": 0.9, "c": 0.1}]
    assert agglomerate(vectors, 0.5) == [[0, 2], [1, 3]]


def test_agglomerate_is_order_deterministic_on_ties() -> None:
    vectors = [{"a": 1.0}] * 4
    assert agglomerate(vectors, 0.5) == [[0, 1, 2, 3]]


# ---- analyse() on the realistic fixtures ------------------------------------------------------


def test_at_least_five_named_niches_each_with_a_status(cards: list[Card]) -> None:
    result = analyse(cards)
    assert len(result.niches) >= 5
    for niche in result.niches:
        assert niche.name and len(niche.keywords) >= 3
        assert niche.status in {"rising", "flat", "saturated"}
        assert niche.size == len(niche.card_ids) >= 3
    assert len({n.id for n in result.niches}) == len(result.niches)


def test_niches_recover_the_six_topics(cards: list[Card]) -> None:
    result = analyse(cards)
    assert len(result.niches) == 6
    by_card = {c.id: c for c in cards}
    for niche in result.niches:
        tags = {by_card[cid].niche_tags[0] for cid in niche.card_ids}
        assert len(tags) == 1, f"{niche.name} mixes {tags}"


def test_names_are_the_top_three_c_tfidf_terms(cards: list[Card]) -> None:
    result = analyse(cards)
    for niche in result.niches:
        assert niche.name == ", ".join(niche.keywords[:3])
    names = " ".join(n.name for n in result.niches)
    for slug in ("invoic", "note", "gpu", "language", "workout", "recipe"):
        assert slug in names, slug


def test_all_three_statuses_appear_on_the_fixtures(cards: list[Card]) -> None:
    result = analyse(cards)
    status_of = {n.name.split(",")[0]: n.status for n in result.niches}
    statuses = {n.status for n in result.niches}
    assert statuses == {"rising", "flat", "saturated"}, status_of
    invoicing = next(n for n in result.niches if "invoice" in n.keywords[:3])
    notes = next(n for n in result.niches if "note" in n.keywords[:3])
    assert invoicing.status == "rising"
    assert notes.status == "saturated"


def test_trend_slope_uses_mentions_and_activity_slope_uses_both(cards: list[Card]) -> None:
    result = analyse(cards)
    notes = next(n for n in result.niches if "note" in n.keywords[:3])
    # notes: flat 1 mention/month (slope 0), launches flat 2/month -> both slopes 0
    assert notes.trend_slope == pytest.approx(0.0, abs=1e-9)
    assert notes.activity_slope == pytest.approx(0.0, abs=1e-9)
    invoicing = next(n for n in result.niches if "invoice" in n.keywords[:3])
    assert invoicing.trend_slope > 0.10


def test_series_matches_the_member_cards(cards: list[Card]) -> None:
    result = analyse(cards)
    by_card = {c.id: c for c in cards}
    for niche in result.niches:
        dated = [by_card[c] for c in niche.card_ids if by_card[c].published_at]
        assert sum(s.launches + s.mentions for s in niche.series) == len(dated)
        months = [s.month for s in niche.series]
        assert months == sorted(months)
        assert months[-1] <= result.as_of  # type: ignore[operator]


def test_as_of_defaults_to_the_newest_card_and_can_be_overridden(cards: list[Card]) -> None:
    assert analyse(cards).as_of == max(c.published_at for c in cards if c.published_at)
    assert analyse(cards, as_of=date(2026, 3, 1)).as_of == date(2026, 3, 1)


def test_outliers_are_unassigned_not_a_niche(cards_with_outliers: list[Card]) -> None:
    result = analyse(cards_with_outliers)
    assert len(result.niches) == 6
    assert len(result.unassigned) == 3
    assigned = {c for n in result.niches for c in n.card_ids}
    assert assigned.isdisjoint(result.unassigned)
    assert len(assigned) + len(result.unassigned) == len(cards_with_outliers)


def test_small_cluster_attaches_to_the_nearest_big_one_when_close_enough() -> None:
    big = [card(i, "alpha beta gamma delta") for i in range(1, 5)]
    stray = card(9, "alpha beta epsilon zeta eta theta")  # shares 2 terms, below merge threshold
    result = analyse(
        [*big, stray], ClusterConfig(merge_threshold=0.9, min_size=3, attach_threshold=0.1)
    )
    assert len(result.niches) == 1
    assert stray.id in result.niches[0].card_ids
    far = analyse(
        [*big, stray], ClusterConfig(merge_threshold=0.9, min_size=3, attach_threshold=0.99)
    )
    assert stray.id in far.unassigned


# ---- tiny and degenerate inputs ---------------------------------------------------------------


def test_zero_cards() -> None:
    result = analyse([])
    assert (result.niches, result.unassigned, result.as_of) == ([], [], None)


def test_one_card_is_unassigned() -> None:
    result = analyse([card(1, "alpha beta")])
    assert result.niches == []
    assert result.unassigned == ["card_00000001"]


def test_min_size_boundary() -> None:
    three = [card(i, "alpha beta gamma") for i in range(3)]
    assert len(analyse(three, ClusterConfig(min_size=3)).niches) == 1
    assert analyse(three, ClusterConfig(min_size=4)).niches == []
    assert len(analyse(three[:2], ClusterConfig(min_size=2)).niches) == 1


def test_cards_without_any_tokens_do_not_crash() -> None:
    blanks = [card(i, "", name="", summary="") for i in range(5)]
    result = analyse(blanks)
    assert result.niches == []
    assert len(result.unassigned) == 5


def test_undated_cards_still_cluster_with_empty_series() -> None:
    undated = [card(i, "alpha beta gamma", published_at=None) for i in range(4)]
    result = analyse(undated)
    assert len(result.niches) == 1
    assert result.niches[0].series == []
    assert result.niches[0].status == "flat"
    assert result.as_of is None


def test_niche_ids_are_unique_even_when_top_terms_collide() -> None:
    ids = niche_ids(
        [
            (["alpha", "beta", "gamma"], ["card_00000001"]),
            (["gamma", "alpha", "beta"], ["card_00000002"]),
            (["alpha", "beta", "delta"], ["card_00000003"]),
        ]
    )
    assert len(set(ids)) == 3
    assert ids == niche_ids(
        [
            (["alpha", "beta", "gamma"], ["card_00000001"]),
            (["gamma", "alpha", "beta"], ["card_00000002"]),
            (["alpha", "beta", "delta"], ["card_00000003"]),
        ]
    )
    assert all(i.startswith("niche_") for i in ids)


# ---- determinism ------------------------------------------------------------------------------


def test_same_input_twice_is_byte_identical(cards: list[Card]) -> None:
    one = analyse(cards)
    two = analyse(cards)
    assert one.model_dump_json() == two.model_dump_json()


def test_shuffled_input_gives_the_same_niches(cards: list[Card]) -> None:
    shuffled = list(cards)
    random.Random(42).shuffle(shuffled)  # noqa: S311
    assert shuffled != cards
    assert analyse(shuffled).model_dump_json() == analyse(cards).model_dump_json()


def test_niche_ids_are_stable_and_derived_from_the_top_terms(cards: list[Card]) -> None:
    ids = [n.id for n in analyse(cards).niches]
    assert all(i.startswith("niche_") and len(i) == len("niche_") + 8 for i in ids)
    assert ids == [n.id for n in analyse(list(reversed(cards))).niches]


# ---- performance -----------------------------------------------------------------------------


def test_clustering_500_synthetic_cards_takes_under_five_seconds() -> None:
    cards = synthetic_cards(500)
    started = time.perf_counter()
    result = analyse(cards)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"took {elapsed:.2f}s"
    assert len(result.niches) >= 20  # 25 topics of 20 cards each
