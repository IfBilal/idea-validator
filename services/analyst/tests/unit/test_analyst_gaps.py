"""Gap detection: complaint clusters no product addresses, always with evidence ids."""

import math
import random
from datetime import date

import pytest
from pydantic import ValidationError

from svc_analyst.fixtures import build_cards
from svc_analyst.models.cards import Card
from svc_analyst.models.domain import Gap, Niche
from svc_analyst.services.clustering import analyse
from svc_analyst.services.gaps import GapConfig, detect_gaps

AS_OF = date(2026, 6, 30)


@pytest.fixture
def cards() -> list[Card]:
    return build_cards()


def card(
    i: int, text: str, kind: str, source: str = "reddit", published: date | None = None
) -> Card:
    return Card.model_validate(
        {
            "id": f"card_{i:08x}",
            "name": "x",
            "text": text,
            "source": source,
            "platforms": [source],
            "kind": kind,
            "published_at": published or date(2026, 6, 1),
        }
    )


def niche_of(cards: list[Card], niche_id: str = "niche_aaaaaaaa") -> Niche:
    return Niche(
        id=niche_id,
        name="n",
        size=max(1, len(cards)),
        trend_slope=0.0,
        activity_slope=0.0,
        status="flat",
        keywords=["alpha"],
        card_ids=[c.id for c in cards],
        series=[],
    )


def complaints(
    n: int, sources: tuple[str, ...] = ("reddit", "github"), text: str = "alpha beta gamma"
) -> list[Card]:
    return [card(i + 1, text, "complaint", sources[i % len(sources)]) for i in range(n)]


def gaps_for(cards: list[Card], **kw: object) -> list[Gap]:
    return detect_gaps([niche_of(cards)], cards, AS_OF, GapConfig(**kw))  # type: ignore[arg-type]


def test_three_complaints_from_two_platforms_make_a_gap() -> None:
    (gap,) = gaps_for(complaints(3))
    assert gap.complaint_count == 3
    assert gap.evidence_ids == ["card_00000001#c0", "card_00000002#c0", "card_00000003#c0"]
    assert gap.niche_id == "niche_aaaaaaaa"
    assert gap.product_coverage == 0
    assert gap.id.startswith("gap_") and len(gap.id) == 12
    assert "alpha" in gap.description or "beta" in gap.description


def test_two_complaints_are_not_a_gap() -> None:
    assert gaps_for(complaints(2)) == []


def test_three_complaints_from_one_platform_are_not_a_gap() -> None:
    assert gaps_for(complaints(3, sources=("reddit",))) == []


def test_requests_count_as_complaints_but_discussions_and_products_do_not() -> None:
    cards = [
        card(1, "alpha beta gamma", "complaint", "reddit"),
        card(2, "alpha beta gamma", "request", "github"),
        card(3, "alpha beta gamma", "discussion", "reddit"),
        card(4, "zebra yak xylophone", "product", "yc"),
    ]
    assert gaps_for(cards) == []  # only two complaint/request cards
    cards.append(card(5, "alpha beta gamma", "request", "reddit"))
    gaps = [g for g in gaps_for(cards) if g.complaint_count == 3]
    assert len(gaps) == 1
    assert "card_00000003#c0" not in gaps[0].evidence_ids


def test_a_product_that_matches_the_complaints_closes_the_gap() -> None:
    cards = [*complaints(3), card(10, "alpha beta gamma", "product", "yc")]
    assert gaps_for(cards) == []


def test_a_related_but_different_product_leaves_the_gap_and_counts_as_coverage() -> None:
    cards = [*complaints(3), card(10, "alpha beta delta epsilon", "product", "yc")]
    (gap,) = gaps_for(cards)
    assert gap.product_coverage == 1


def test_coverage_threshold_is_inclusive() -> None:
    cards = [*complaints(3), card(10, "alpha beta delta epsilon", "product", "yc")]
    assert gaps_for(cards, coverage_threshold=0.99) != []
    assert gaps_for(cards, coverage_threshold=0.30) == []


def test_unrelated_product_does_not_count_as_coverage() -> None:
    cards = [*complaints(3), card(10, "zebra yak xylophone", "product", "yc")]
    (gap,) = gaps_for(cards)
    assert gap.product_coverage == 0


def test_two_themes_make_two_gaps() -> None:
    a = complaints(3, text="alpha beta gamma")
    b = [card(20 + i, "delta epsilon zeta", "complaint", ("reddit", "yc")[i % 2]) for i in range(4)]
    gaps = gaps_for([*a, *b])
    assert sorted(g.complaint_count for g in gaps) == [3, 4]


def test_score_is_complaints_times_log_sources_times_recency() -> None:
    cards = [
        card(1, "alpha beta gamma", "complaint", "reddit", date(2026, 6, 1)),
        card(2, "alpha beta gamma", "complaint", "github", date(2026, 5, 1)),
        card(3, "alpha beta gamma", "complaint", "yc", date(2026, 1, 1)),
        card(4, "alpha beta gamma", "complaint", "reddit", date(2024, 1, 1)),
    ]
    (gap,) = gaps_for(cards)
    assert gap.score == pytest.approx(4 * math.log(1 + 3) * 0.75, abs=1e-5)


def test_recency_window_is_inclusive_at_twelve_months() -> None:
    cards = [
        card(1, "alpha beta gamma", "complaint", "reddit", date(2025, 6, 30)),  # exactly 12 months
        card(2, "alpha beta gamma", "complaint", "github", date(2025, 6, 29)),  # one day older
        card(3, "alpha beta gamma", "complaint", "yc", date(2025, 6, 29)),
    ]
    (gap,) = gaps_for(cards)
    assert gap.score == pytest.approx(3 * math.log(4) * (1 / 3), abs=1e-5)


def test_old_gaps_have_zero_score_but_still_carry_evidence() -> None:
    cards = [
        card(i, "alpha beta gamma", "complaint", ("reddit", "github")[i % 2], date(2020, 1, 1))
        for i in range(1, 4)
    ]
    (gap,) = gaps_for(cards)
    assert gap.score == 0.0
    assert gap.evidence_ids


def test_gap_model_refuses_to_exist_without_evidence() -> None:
    with pytest.raises(ValidationError):
        Gap(
            id="gap_x",
            niche_id="n",
            description="d",
            evidence_ids=[],
            complaint_count=3,
            product_coverage=0,
            score=1.0,
        )


def test_empty_inputs() -> None:
    assert detect_gaps([], [], AS_OF) == []
    assert detect_gaps([niche_of([])], [], AS_OF) == []


def test_unknown_member_ids_are_skipped_not_fatal() -> None:
    cards = complaints(3)
    niche = niche_of(cards).model_copy(
        update={"card_ids": [*niche_of(cards).card_ids, "card_deadbeef"]}
    )
    assert len(detect_gaps([niche], cards, AS_OF)) == 1


def test_no_as_of_means_no_recency_credit() -> None:
    (gap,) = detect_gaps([niche_of(complaints(3))], complaints(3), None)
    assert gap.score == 0.0


# ---- on the realistic fixtures ------------------------------------------------------------------


def test_every_fixture_niche_has_gaps_each_with_valid_evidence(cards: list[Card]) -> None:
    analysis = analyse(cards)
    gaps = detect_gaps(analysis.niches, cards, analysis.as_of)
    by_id = {c.id: c for c in cards}
    assert {g.niche_id for g in gaps} == {n.id for n in analysis.niches}
    for gap in gaps:
        assert gap.evidence_ids
        assert gap.complaint_count == len(gap.evidence_ids) >= 3
        for evidence_id in gap.evidence_ids:
            cid, chunk = evidence_id.split("#")
            assert chunk == "c0"
            assert by_id[cid].is_complaint
        niche = next(n for n in analysis.niches if n.id == gap.niche_id)
        assert {e.split("#")[0] for e in gap.evidence_ids} <= set(niche.card_ids)
        platforms = set().union(*(by_id[e.split("#")[0]].all_platforms for e in gap.evidence_ids))
        assert len(platforms) >= 2
        assert gap.score >= 0


def test_gaps_are_sorted_by_score_and_deterministic(cards: list[Card]) -> None:
    analysis = analyse(cards)
    one = detect_gaps(analysis.niches, cards, analysis.as_of)
    two = detect_gaps(analysis.niches, cards, analysis.as_of)
    assert [g.model_dump_json() for g in one] == [g.model_dump_json() for g in two]
    scores = [(-g.score, g.id) for g in one]
    assert scores == sorted(scores)
    assert len({g.id for g in one}) == len(one)


def test_shuffled_cards_give_the_same_gaps(cards: list[Card]) -> None:
    analysis = analyse(cards)
    shuffled = list(cards)
    random.Random(42).shuffle(shuffled)  # noqa: S311
    a = detect_gaps(analysis.niches, cards, analysis.as_of)
    b = detect_gaps(analysis.niches, shuffled, analysis.as_of)
    assert [g.model_dump_json() for g in a] == [g.model_dump_json() for g in b]
