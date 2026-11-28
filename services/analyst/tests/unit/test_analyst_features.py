"""Every M7 feature of docs/harness/09 §4: boundaries, ties, empties, determinism."""

import math
from datetime import date

import pytest

from svc_analyst.models.cards import Card
from svc_analyst.models.domain import Gap
from svc_analyst.services.features import (
    EvidenceRef,
    Scored,
    available_hours,
    card_strength,
    complaint_count,
    compute_fit,
    cv_signal,
    duplicate_of,
    estimated_effort_hours,
    extract_required_skills,
    infer_scope,
    max_similarity,
    n_competitors,
    n_platforms,
    n_sources,
    needs_gpu,
    niche_saturation,
    platform_dependency,
    recency_share,
    required_compute,
    risk_blocker,
    risk_flags,
    same_user,
    skill_novelty,
    skill_overlap,
    target_users,
    trend_growth,
    unaddressed_gap_count,
)

AS_OF = date(2026, 6, 15)


def ev(
    n: int,
    *,
    card: int | None = None,
    kind: str | None = "complaint",
    platforms: tuple[str, ...] = ("reddit",),
    published: date | None = date(2026, 6, 1),
    score: float | None = None,
) -> EvidenceRef:
    card_n = n if card is None else card
    return EvidenceRef(
        evidence_id=f"card_{card_n:08x}#c{n}",
        card_id=f"card_{card_n:08x}",
        kind=kind,
        platforms=frozenset(platforms),
        published_at=published,
        score=score,
    )


def card(**kw: object) -> Card:
    base: dict[str, object] = {"id": "card_00000001", "name": "x", "source": "github"}
    base.update(kw)
    return Card.model_validate(base)


def scored(cos: float, jac: float = 0.0, *, user: bool = True, **kw: object) -> Scored:
    return Scored(card=card(**kw), cosine=cos, jaccard=jac, same_user=user)


# ---- n_sources / n_platforms / complaint_count -------------------------------------------------


def test_counts_on_empty_evidence_are_zero() -> None:
    assert n_sources([]) == 0
    assert n_platforms([]) == 0
    assert complaint_count([]) == 0


def test_n_sources_counts_distinct_cards_not_chunks() -> None:
    items = [ev(1, card=1), ev(2, card=1), ev(3, card=2)]
    assert n_sources(items) == 2


def test_n_platforms_is_distinct_over_all_cards() -> None:
    items = [
        ev(1, platforms=("reddit", "hackernews")),
        ev(2, platforms=("reddit",)),
        ev(3, platforms=("github",)),
    ]
    assert n_platforms(items) == 3


def test_single_card_single_platform() -> None:
    assert (n_sources([ev(1)]), n_platforms([ev(1)])) == (1, 1)


def test_complaint_count_counts_chunks_of_complaint_and_request_only() -> None:
    items = [
        ev(1, kind="complaint"),
        ev(2, kind="request"),
        ev(3, kind="product"),
        ev(4, kind="discussion"),
        ev(5, kind=None),
        ev(6, kind="repo"),
    ]
    assert complaint_count(items) == 2


def test_complaint_count_counts_two_chunks_of_one_card_twice_but_not_duplicates() -> None:
    a, b = ev(1, card=1), ev(2, card=1)
    assert complaint_count([a, b, a]) == 2


def test_relevance_threshold_is_inclusive_and_unscored_items_are_kept() -> None:
    items = [ev(1, score=0.5), ev(2, score=0.4999), ev(3, score=None)]
    assert n_sources(items, tau=0.5) == 2  # 0.5 (exactly tau) and the unscored one
    assert n_sources(items) == 3  # no tau: everything counts
    assert complaint_count(items, tau=0.5) == 2
    assert n_platforms(items, tau=0.5) == 1


# ---- recency_share ---------------------------------------------------------------------------


def test_recency_share_empty_is_zero() -> None:
    assert recency_share([], AS_OF) == 0.0


def test_recency_share_exactly_twelve_months_counts_as_recent() -> None:
    edge = date(2025, 6, 15)
    just_before = date(2025, 6, 14)
    items = [ev(1, published=edge), ev(2, published=just_before)]
    assert recency_share(items, AS_OF) == 0.5


def test_recency_share_unknown_dates_are_not_recent_and_future_dates_are() -> None:
    items = [ev(1, published=None), ev(2, published=date(2026, 7, 1)), ev(3, published=AS_OF)]
    assert recency_share(items, AS_OF) == pytest.approx(2 / 3)


def test_recency_share_leap_day_cutoff() -> None:
    as_of = date(2028, 2, 29)
    items = [ev(1, published=date(2027, 2, 28)), ev(2, published=date(2027, 2, 27))]
    assert recency_share(items, as_of) == 0.5


def test_recency_share_all_recent_and_none_recent() -> None:
    assert recency_share([ev(1), ev(2)], AS_OF) == 1.0
    assert recency_share([ev(1, published=date(2020, 1, 1))], AS_OF) == 0.0


# ---- trend_growth / niche_saturation -----------------------------------------------------------


def test_trend_growth_passes_slope_and_defaults_to_zero() -> None:
    assert trend_growth(0.25) == 0.25
    assert trend_growth(-0.3) == -0.3
    assert trend_growth(None) == 0.0


@pytest.mark.parametrize(
    ("status", "size", "sizes", "expected"),
    [
        ("saturated", 10, [1, 5, 10], 1.0),  # saturated + largest
        ("rising", 1, [1, 5, 10], 0.0),  # rising + smallest
        ("flat", 5, [1, 5, 10], 0.5),
        ("flat", 7, [7], 0.5),  # single niche: neutral percentile
        ("saturated", 7, [7], 0.75),
        ("rising", 5, [5, 5, 5], 0.0 + 0.25),  # all tied: percentile 0.5
        (None, 5, [1, 5, 10], 0.5),
    ],
)
def test_niche_saturation_cases(
    status: str | None, size: int, sizes: list[int], expected: float
) -> None:
    assert niche_saturation(status, size, sizes) == pytest.approx(expected)


def test_niche_saturation_unknown_niche_is_neutral() -> None:
    assert niche_saturation(None, None, []) == 0.5


# ---- competitors -----------------------------------------------------------------------------


def test_n_competitors_threshold_is_inclusive_at_080() -> None:
    items = [scored(0.80), scored(0.7999), scored(0.81), scored(0.5)]
    assert n_competitors(items) == 2


def test_n_competitors_requires_same_target_user() -> None:
    assert n_competitors([scored(0.9, user=False), scored(0.9)]) == 1


def test_n_competitors_ignores_jaccard_and_handles_empty() -> None:
    assert n_competitors([scored(0.1, jac=0.99)]) == 0
    assert n_competitors([]) == 0


def test_card_strength_components() -> None:
    # launched, no funding, no stars: (0 + 0.3 + 0) / 3
    assert card_strength(card(stage="launched")) == pytest.approx(0.1)
    # acquired with 1e9 funding and 1e5 stars saturates every term
    big = card(stage="acquired", funding_usd=1_000_000_000, github_stars=100_000)
    assert card_strength(big) == pytest.approx(1.0)
    over = card(stage="acquired", funding_usd=10**12, github_stars=10**8)
    assert card_strength(over) == pytest.approx(1.0)  # terms are capped
    dead = card(stage="dead")
    assert card_strength(dead) == 0.0


def test_card_strength_funding_term_is_log_scaled() -> None:
    c = card(stage="dead", funding_usd=10_000_000)
    expected = (math.log(1 + 10_000_000) / math.log(1 + 1e9)) / 3
    assert card_strength(c) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("stage", "weight"),
    [
        ("dead", 0.0),
        ("unknown", 0.3),
        ("launched", 0.3),
        ("seed", 0.5),
        ("series_a", 0.7),
        ("series_b_plus", 0.9),
        ("acquired", 1.0),
    ],
)
def test_stage_weights(stage: str, weight: float) -> None:
    assert card_strength(card(stage=stage)) == pytest.approx(weight / 3)


def test_competitor_strength_is_mean_over_competitors_only() -> None:
    from svc_analyst.services.features import competitor_strength

    items = [
        scored(0.9, stage="seed"),
        scored(0.85, stage="acquired"),
        scored(0.2, stage="acquired"),  # not a competitor
    ]
    assert competitor_strength(items) == pytest.approx((0.5 / 3 + 1.0 / 3) / 2)
    assert competitor_strength([]) == 0.0
    assert competitor_strength([scored(0.1)]) == 0.0


# ---- max_similarity --------------------------------------------------------------------------


def test_max_similarity_takes_the_stronger_of_cosine_and_core_feature_jaccard() -> None:
    items = [scored(0.3, 0.9), scored(0.6, 0.1)]
    assert max_similarity(items) == 0.9
    assert max_similarity([scored(0.95, 0.2)]) == 0.95


def test_max_similarity_empty_and_ties() -> None:
    assert max_similarity([]) == 0.0
    assert max_similarity([scored(0.5), scored(0.5)]) == 0.5


# ---- duplicate_of / risk_blocker (inputs of the M11 gates) ---------------------------------------


def test_duplicate_of_names_the_most_similar_card_of_the_same_user_at_or_above_the_threshold() -> (
    None
):
    items = [
        scored(0.7, 0.2, id="card_00000010"),
        scored(0.9, 0.1, id="card_00000011"),
        scored(0.86, 0.0, id="card_00000012"),
    ]
    assert duplicate_of(items) == "card_00000011"
    assert duplicate_of([scored(0.85, 0.0, id="card_00000013")]) == "card_00000013"  # inclusive


def test_duplicate_of_is_none_below_the_threshold_for_another_user_or_with_no_cards() -> None:
    assert duplicate_of([]) is None
    assert duplicate_of([scored(0.84, 0.84)]) is None
    assert duplicate_of([scored(0.99, 0.99, user=False)]) is None, (
        "another target user is not a clone"
    )


def test_duplicate_of_counts_the_core_feature_jaccard_too_and_breaks_ties_by_id() -> None:
    assert duplicate_of([scored(0.1, 0.9, id="card_00000020")]) == "card_00000020"
    tie = [scored(0.9, id="card_00000031"), scored(0.9, id="card_00000030")]
    assert duplicate_of(tie) == "card_00000030"


def test_risk_blocker_is_the_blocker_flag_itself_never_free_text() -> None:
    assert risk_blocker(["blocker", "pii", "scraping_logged_in"]) == "scraping_logged_in"
    assert risk_blocker(["pii", "minors"]) is None
    assert risk_blocker([]) is None
    assert risk_blocker(risk_flags("scrape LinkedIn behind login")) == "scraping_logged_in"


# ---- gaps ------------------------------------------------------------------------------------


def gap(n: int, evidence: str) -> Gap:
    return Gap(
        id=f"gap_{n}",
        niche_id="n1",
        description="d",
        evidence_ids=[evidence],
        complaint_count=3,
        product_coverage=0,
        score=1.0,
    )


def test_unaddressed_gap_count_counts_relevant_gaps_only() -> None:
    gaps = [gap(1, "card_00000001#c0"), gap(2, "card_00000002#c0"), gap(3, "card_00000003#c0")]
    relevance = {"gap_1": 0.25, "gap_2": 0.2499, "gap_3": 0.5}
    assert unaddressed_gap_count(gaps, relevance, set()) == 2  # 0.25 is inclusive


def test_unaddressed_gap_count_cited_evidence_makes_a_gap_relevant() -> None:
    gaps = [gap(1, "card_00000001#c0")]
    assert unaddressed_gap_count(gaps, {"gap_1": 0.0}, {"card_00000001#c0"}) == 1
    assert unaddressed_gap_count(gaps, {"gap_1": 0.0}, {"card_00000009#c0"}) == 0
    assert unaddressed_gap_count([], {}, set()) == 0


# ---- profile maths ---------------------------------------------------------------------------


def test_skill_overlap_is_case_insensitive_and_bounded() -> None:
    assert skill_overlap(["Python", "SQL"], ["python", "react"]) == 0.5
    assert skill_overlap(["python"], ["python"]) == 1.0
    assert skill_overlap([], ["python"]) == 0.0
    assert skill_overlap(["go"], ["python", "react"]) == 0.0


def test_skill_overlap_no_required_skills_is_neutral() -> None:
    assert skill_overlap(["python"], []) == 0.5


def test_skill_overlap_dedupes_required() -> None:
    assert skill_overlap(["python"], ["Python", "python", "react"]) == 0.5


@pytest.mark.parametrize(
    ("hpw", "weeks", "team", "expected"),
    [(10, 8, 2, 160), (0, 8, 3, 0), (80, 52, 10, 41600), (1, 1, 1, 1)],
)
def test_available_hours(hpw: int, weeks: int, team: int, expected: int) -> None:
    assert available_hours(hpw, weeks, team) == expected


@pytest.mark.parametrize(
    ("scope", "overlap", "gpu", "compute", "expected"),
    [
        ("S", 1.0, False, "laptop", 60.0),
        ("M", 1.0, False, "laptop", 160.0),
        ("L", 1.0, False, "laptop", 400.0),
        ("S", 0.0, False, "laptop", 90.0),  # x1.5
        ("M", 0.0, False, "laptop", 240.0),
        ("L", 0.0, False, "laptop", 600.0),
        ("M", 0.5, False, "laptop", 200.0),  # x1.25
        ("M", 1.0, True, "laptop", 240.0),  # GPU needed on a laptop: x1.5
        ("M", 0.0, True, "laptop", 360.0),  # both factors
        ("M", 1.0, True, "free_gpu", 160.0),  # a GPU is available: no penalty
        ("M", 1.0, True, "paid", 160.0),
        ("M", 1.0, False, "free_gpu", 160.0),
    ],
)
def test_estimated_effort_hours(
    scope: str, overlap: float, gpu: bool, compute: str, expected: float
) -> None:
    assert estimated_effort_hours(scope, overlap, gpu, compute) == pytest.approx(expected)


def test_estimated_effort_hours_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="scope"):
        estimated_effort_hours("XL", 0.5, False, "laptop")
    with pytest.raises(ValueError, match="skill_overlap"):
        estimated_effort_hours("S", 1.01, False, "laptop")
    with pytest.raises(ValueError, match="skill_overlap"):
        estimated_effort_hours("S", -0.01, False, "laptop")


@pytest.mark.parametrize(
    ("required", "have", "expected"),
    [
        ("laptop", "laptop", True),
        ("laptop", "free_gpu", True),
        ("laptop", "paid", True),
        ("free_gpu", "laptop", False),
        ("free_gpu", "free_gpu", True),
        ("free_gpu", "paid", True),
        ("paid", "laptop", False),
        ("paid", "free_gpu", False),
        ("paid", "paid", True),
    ],
)
def test_compute_fit_matrix(required: str, have: str, expected: bool) -> None:
    assert compute_fit(required, have) is expected


def test_compute_fit_rejects_unknown_levels() -> None:
    with pytest.raises(ValueError, match="compute"):
        compute_fit("quantum", "laptop")


def test_required_compute_and_needs_gpu_from_text() -> None:
    assert required_compute("a todo list web app") == "laptop"
    assert required_compute("fine-tune a small LLM on notes") == "free_gpu"
    assert required_compute("Train on an A100 cluster") == "paid"
    assert required_compute("pretrain a 70B model") == "paid"
    assert needs_gpu("a todo list web app") is False
    assert needs_gpu("uses CUDA for video upscaling") is True
    assert needs_gpu("fine-tune a model") is True
    assert needs_gpu("") is False


def test_gpu_keywords_need_word_boundaries() -> None:
    assert needs_gpu("cudaless is not a word") is False


# ---- scope / skills extraction ------------------------------------------------------------------


def test_infer_scope_boundaries() -> None:
    assert infer_scope("") == "S"
    assert infer_scope("A tracker for invoices.") == "S"
    # 3 clauses -> still S (points 3), 4 -> M, 8 -> M, 9 -> L
    three = "invoice tracker, reminder emails, csv export"
    assert infer_scope(three) == "S"
    four = three + ", usage dashboard"
    assert infer_scope(four) == "M"
    eight = ", ".join(f"feature number{i} thing" for i in range(8))
    assert infer_scope(eight) == "M"
    nine = ", ".join(f"feature number{i} thing" for i in range(9))
    assert infer_scope(nine) == "L"


def test_infer_scope_complex_keywords_count_double() -> None:
    assert infer_scope("a marketplace") == "S"  # 1 clause + 2
    assert infer_scope("a marketplace, real-time chat") == "M"  # 2 + 4 = 6
    assert (
        infer_scope("a marketplace with real-time chat, mobile app, ml pipeline, payments") == "L"
    )


def test_extract_required_skills_from_text_and_profile() -> None:
    text = "A FastAPI service in Python with React frontend and machine learning ranking"
    assert extract_required_skills(text, ["Rust"]) == [
        "fastapi",
        "machine learning",
        "python",
        "react",
    ]
    assert extract_required_skills("uses rust for speed", ["Rust"]) == ["rust"]
    assert extract_required_skills("just words", []) == []
    assert extract_required_skills("", []) == []


def test_extract_required_skills_word_boundaries() -> None:
    assert extract_required_skills("a gopher cat", []) == []
    assert extract_required_skills("written in Go.", []) == []  # "go" alone is too ambiguous
    assert extract_required_skills("written in Go.", ["Go"]) == ["go"]  # unless the profile has it
    assert extract_required_skills("written in golang", []) == ["go"]
    assert extract_required_skills("a go/no-go tool", []) == []
    assert extract_required_skills("javascripty", []) == []


# ---- risk ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "flags"),
    [
        ("a recipe organiser", []),
        ("track patient medical records", ["health_data"]),
        ("a kids learning app for children under 13", ["minors"]),
        ("automated stock trading bot", ["finance_trading"]),
        ("scrape LinkedIn behind login", ["blocker", "scraping_logged_in"]),
        ("store user email addresses and phone numbers", ["pii"]),
        ("collect SSN and passport data", ["pii"]),
        ("a Twitter API powered digest", ["tos_dependent_api"]),
        (
            "health data for minors with trading signals",
            ["finance_trading", "health_data", "minors"],
        ),
    ],
)
def test_risk_flags(text: str, flags: list[str]) -> None:
    assert risk_flags(text) == flags


def test_risk_flags_are_sorted_unique_and_case_insensitive() -> None:
    assert risk_flags("HEALTH data, health data, MINORS") == ["health_data", "minors"]
    assert risk_flags("") == []


def test_risk_flags_do_not_match_inside_words() -> None:
    assert risk_flags("a tradingview widget") == []
    assert risk_flags("childhood memories journal") == []


def test_platform_dependency_is_exactly_one_named_platform() -> None:
    assert platform_dependency("a digest built on the Twitter API") is True
    assert platform_dependency("a Slack bot") is True
    assert platform_dependency("syncs Slack with Notion") is False  # two platforms: not one
    assert platform_dependency("a standalone invoice tool") is False
    assert platform_dependency("") is False


# ---- learning --------------------------------------------------------------------------------


def test_skill_novelty_counts_unknown_or_interesting_required_skills() -> None:
    # python known and not an interest -> not novel; rust unknown -> novel;
    # react known but an interest -> novel; sql unknown -> novel
    assert skill_novelty(["python", "rust", "react", "sql"], ["python", "react"], ["react"]) == 3


def test_skill_novelty_edges() -> None:
    assert skill_novelty([], ["python"], ["x"]) == 0
    assert skill_novelty(["python"], ["python"], []) == 0
    assert skill_novelty(["PYTHON"], ["python"], []) == 0
    assert skill_novelty(["rust", "rust"], [], []) == 1


def test_cv_signal_blends_in_demand_share_and_trend() -> None:
    demand = ["python", "react"]
    # all required in demand (1.0) and trend 0 -> 0.5*1 + 0.5*0.5
    assert cv_signal(["python", "react"], demand, 0.0) == pytest.approx(0.75)
    # none in demand, trend >= 0.5 saturates at 1
    assert cv_signal(["cobol"], demand, 0.5) == pytest.approx(0.5)
    assert cv_signal(["cobol"], demand, 5.0) == pytest.approx(0.5)
    # strongly negative trend floors the trend term at 0
    assert cv_signal(["python"], demand, -9.0) == pytest.approx(0.5)
    assert cv_signal([], demand, 0.0) == pytest.approx(0.25)
    assert cv_signal(["python", "cobol"], demand, 0.0) == pytest.approx(0.5)


def test_cv_signal_is_within_unit_interval() -> None:
    for trend in (-100.0, -0.5, 0.0, 0.5, 100.0):
        value = cv_signal(["python"], ["python"], trend)
        assert 0.0 <= value <= 1.0


# ---- target users --------------------------------------------------------------------------------


def test_target_users_extracts_the_phrase_after_for() -> None:
    assert target_users("An invoice tool for freelance designers.") == {"freelance", "designer"}
    assert target_users("tool for teachers, with exports") == {"teacher"}
    assert target_users("no audience mentioned") == frozenset()


def test_same_user_is_lenient_when_a_side_is_unknown() -> None:
    assert same_user(frozenset(), frozenset({"teacher"})) is True
    assert same_user(frozenset({"teacher"}), frozenset()) is True
    assert same_user(frozenset({"teacher"}), frozenset({"teacher", "tutor"})) is True
    assert same_user(frozenset({"teacher"}), frozenset({"nurse"})) is False
