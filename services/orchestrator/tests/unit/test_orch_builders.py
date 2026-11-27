"""Pure builders: evidence handling, score parsing, verdict requests, claims, unknowns."""

from typing import Any

import pytest

from common.schemas import DimensionScore, EvidenceItem
from common.schemas.types import DIMENSIONS
from svc_orchestrator.models.api import UnknownSpec
from svc_orchestrator.services import builders

RUBRICS = {d: f"{d}@v1" for d in DIMENSIONS}
E1, E2, E3 = "card_8f21a0b1#c0", "card_8f21a0b1#c2", "card_77aa00bb#c1"


def evidence(eid: str, source: str = "hackernews") -> EvidenceItem:
    return EvidenceItem.model_validate(
        {
            "evidence_id": eid,
            "card_id": eid.split("#")[0],
            "quote": f"quote {eid}",
            "source": source,
            "url": "https://example.com/x",
            "retrieved_at": "2026-05-01T00:00:00Z",
        }
    )


def raw_score(dim: str, score: float | None = 3.0, ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "dimension": dim,
        "score": score,
        "confidence": 0.8 if score is not None else 0,
        "evidence_ids": [E1] if ids is None else ids,
        "reason": "because",
        "rubric_version": f"{dim}@v1",
        "model": "m10",
    }


def full_raw() -> list[dict[str, Any]]:
    return [raw_score(d) for d in DIMENSIONS]


# ---- queries and evidence ------------------------------------------------------------------
def test_four_queries_cover_problem_competitors_complaints_and_demand() -> None:
    queries = builders.evidence_queries("A CLI that lints Dockerfiles")
    assert len(queries) == 4
    lowered = [q.lower() for q in queries]
    assert any("competitor" in q or "alternative" in q for q in lowered)
    assert any("complain" in q or "frustrat" in q for q in lowered)
    assert any("demand" in q or "looking for" in q for q in lowered)
    assert all("A CLI that lints Dockerfiles" in q for q in queries)


def test_long_ideas_are_truncated_in_queries() -> None:
    queries = builders.evidence_queries("x" * 4000)
    assert all(len(q) <= 340 for q in queries)


def test_merge_evidence_dedupes_by_id_keeping_first_occurrence_order() -> None:
    merged = builders.merge_evidence([[evidence(E1), evidence(E2)], [evidence(E2), evidence(E3)]])
    assert [e.evidence_id for e in merged] == [E1, E2, E3]


def test_merge_evidence_of_nothing_is_empty() -> None:
    assert builders.merge_evidence([[], []]) == []


@pytest.mark.parametrize(
    ("items", "sources", "platforms"),
    [
        ([], 0, 0),
        ([evidence(E1)], 1, 1),
        ([evidence(E1), evidence(E2)], 1, 1),  # two chunks of one card are one source
        ([evidence(E1), evidence(E3, "github")], 2, 2),
        ([evidence(E1), evidence(E3)], 2, 1),
    ],
)
def test_evidence_stats_count_distinct_cards_and_platforms(
    items: list[EvidenceItem], sources: int, platforms: int
) -> None:
    assert builders.evidence_stats(items) == (sources, platforms)


@pytest.mark.parametrize(
    ("sources", "platforms", "thin"),
    [(3, 2, False), (2, 2, True), (3, 1, True), (0, 0, True), (10, 5, False)],
)
def test_thin_evidence_boundaries(sources: int, platforms: int, thin: bool) -> None:
    assert builders.is_thin(sources, platforms) is thin


def test_scorer_evidence_payload_has_only_id_and_quote() -> None:
    assert builders.scorer_evidence([evidence(E1)]) == [{"evidence_id": E1, "quote": f"quote {E1}"}]


# ---- parsing scores: evidence or abstain ---------------------------------------------------
def by_dim(scores: list[DimensionScore]) -> dict[str, DimensionScore]:
    return {s.dimension: s for s in scores}


def test_a_full_valid_response_yields_six_scores_in_canonical_order() -> None:
    scores = builders.parse_scores(full_raw()[::-1], {E1}, RUBRICS)
    assert [s.dimension for s in scores] == list(DIMENSIONS)
    assert all(s.score == 3.0 for s in scores)


def test_missing_dimension_becomes_an_abstention() -> None:
    raw = [r for r in full_raw() if r["dimension"] != "risk"]
    risk = by_dim(builders.parse_scores(raw, {E1}, RUBRICS))["risk"]
    assert risk.score is None
    assert risk.confidence == 0
    assert risk.evidence_ids == []
    assert risk.abstained is True
    assert risk.rubric_version == "risk@v1"


def test_invalid_scorer_json_items_abstain_only_that_dimension() -> None:
    raw: list[Any] = full_raw()
    raw[1] = "not an object"
    raw[2] = {"dimension": "differentiation", "score": 99, "confidence": 2}
    got = by_dim(builders.parse_scores(raw, {E1}, RUBRICS))
    assert got["competition"].score is None
    assert got["differentiation"].score is None
    assert got["demand_pain"].score == 3.0


def test_unknown_dimension_names_are_ignored() -> None:
    raw = [*full_raw(), {**raw_score("demand_pain"), "dimension": "vibes"}]
    assert len(builders.parse_scores(raw, {E1}, RUBRICS)) == 6


def test_duplicate_dimension_keeps_the_first_valid_one() -> None:
    first = {**raw_score("risk"), "score": 1.0}
    second = {**raw_score("risk"), "score": 5.0}
    got = by_dim(builders.parse_scores([first, second], {E1}, RUBRICS))
    assert got["risk"].score == 1.0


def test_evidence_ids_not_handed_to_the_scorer_are_dropped() -> None:
    raw = [raw_score("risk", ids=[E1, "card_deadbeef#c9"])]
    risk = by_dim(builders.parse_scores(raw, {E1}, RUBRICS))["risk"]
    assert risk.evidence_ids == [E1]
    assert risk.score == 3.0


def test_a_score_whose_evidence_is_all_invented_abstains() -> None:
    raw = [raw_score("risk", ids=["card_deadbeef#c9"])]
    risk = by_dim(builders.parse_scores(raw, {E1}, RUBRICS))["risk"]
    assert risk.score is None
    assert risk.evidence_ids == []


def test_a_non_null_score_with_no_evidence_abstains() -> None:
    raw = [raw_score("risk", ids=[])]
    assert by_dim(builders.parse_scores(raw, {E1}, RUBRICS))["risk"].score is None


def test_a_scorer_abstention_is_kept_as_an_abstention() -> None:
    raw = [raw_score("risk", score=None, ids=[])]
    risk = by_dim(builders.parse_scores(raw, {E1}, RUBRICS))["risk"]
    assert risk.score is None
    assert risk.abstained is True


@pytest.mark.parametrize("score", [0.0, 5.0])
def test_score_boundaries_are_accepted(score: float) -> None:
    got = by_dim(builders.parse_scores([raw_score("risk", score=score)], {E1}, RUBRICS))
    assert got["risk"].score == score


@pytest.mark.parametrize("score", [-0.1, 5.1])
def test_scores_outside_zero_to_five_abstain(score: float) -> None:
    got = by_dim(builders.parse_scores([raw_score("risk", score=score)], {E1}, RUBRICS))
    assert got["risk"].score is None


def test_abstain_all_gives_six_abstentions() -> None:
    scores = builders.abstain_all(RUBRICS, "scorer unavailable")
    assert [s.dimension for s in scores] == list(DIMENSIONS)
    assert all(s.score is None and s.abstained for s in scores)
    assert "scorer unavailable" in scores[0].reason


def test_abstain_reason_is_capped_to_the_schema_limit() -> None:
    assert len(builders.abstain_all(RUBRICS, "x" * 2000)[0].reason) <= 600


def test_rubric_version_falls_back_when_m11_did_not_list_one() -> None:
    assert builders.abstain_all({}, "r")[0].rubric_version == "demand_pain@v1"


# ---- gate inputs, answers and the verdict request ------------------------------------------
PROFILE = {"goal": "startup", "hours_per_week": 10, "weeks_available": 12, "team_size": 2}


def request(**kw: Any) -> dict[str, Any]:
    scores = builders.parse_scores(full_raw(), {E1}, RUBRICS)
    args: dict[str, Any] = {
        "idea_id": "idea_1",
        "version": 2,
        "run_id": "run_1",
        "profile": PROFILE,
        "scores": scores,
        "n_sources": 4,
        "n_platforms": 2,
        "extra": {"estimated_effort_hours": 100, "risk_blocker": None, "duplicate_of": None},
        "overrides": {},
        "answers": {},
        "objections": [],
    }
    return builders.build_verdict_request(**{**args, **kw})


def test_verdict_request_has_the_m11_shape() -> None:
    body = request()
    assert set(body) == {
        "idea_id",
        "version",
        "run_id",
        "profile",
        "scores",
        "gate_inputs",
        "objections_accepted",
    }
    assert body["profile"] == PROFILE
    assert body["gate_inputs"] == {
        "n_sources": 4,
        "n_platforms": 2,
        "estimated_effort_hours": 100,
        "risk_blocker": None,
        "duplicate_of": None,
    }
    assert [s["dimension"] for s in body["scores"]] == list(DIMENSIONS)
    assert body["scores"][0]["score"] == 3.0


def test_abstained_scores_are_sent_as_explicit_nulls() -> None:
    scores = builders.abstain_all(RUBRICS, "x")
    body = request(scores=scores)
    assert body["scores"][0]["score"] is None
    assert body["scores"][0]["confidence"] == 0


def test_user_overrides_beat_the_scorer_estimate_and_answers_beat_both() -> None:
    assert (
        request(overrides={"estimated_effort_hours": 60})["gate_inputs"]["estimated_effort_hours"]
        == 60
    )
    both = request(
        overrides={"estimated_effort_hours": 60}, answers={"estimated_effort_hours": 200.0}
    )
    assert both["gate_inputs"]["estimated_effort_hours"] == 200.0


def test_an_unanswered_question_leaves_the_estimate_untouched() -> None:
    body = request(answers={"estimated_effort_hours": None})
    assert body["gate_inputs"]["estimated_effort_hours"] == 100


def test_profile_answers_change_the_profile_block() -> None:
    body = request(answers={"hours_per_week": 25.0})
    assert body["profile"]["hours_per_week"] == 25.0


def test_score_answers_replace_that_dimensions_score() -> None:
    body = request(answers={"competition": 4.5})
    assert {s["dimension"]: s["score"] for s in body["scores"]}["competition"] == 4.5


def test_missing_effort_defaults_to_zero_and_is_flagged() -> None:
    body = request(extra={})
    assert body["gate_inputs"]["estimated_effort_hours"] == 0
    assert builders.effort_is_unknown({}, {}, {}) is True
    assert builders.effort_is_unknown({"estimated_effort_hours": 5}, {}, {}) is False
    assert builders.effort_is_unknown({}, {"estimated_effort_hours": 5}, {}) is False
    assert builders.effort_is_unknown({}, {}, {"estimated_effort_hours": 5.0}) is False


def test_extra_with_wrong_types_is_ignored_not_trusted() -> None:
    body = request(extra={"estimated_effort_hours": "lots", "risk_blocker": 5, "duplicate_of": []})
    assert body["gate_inputs"]["estimated_effort_hours"] == 0
    assert body["gate_inputs"]["risk_blocker"] is None
    assert body["gate_inputs"]["duplicate_of"] is None


def test_risk_blocker_and_duplicate_are_forwarded_as_strings() -> None:
    body = request(
        extra={
            "estimated_effort_hours": 1,
            "risk_blocker": "needs a licence",
            "duplicate_of": "card_1",
        }
    )
    assert body["gate_inputs"]["risk_blocker"] == "needs a licence"
    assert body["gate_inputs"]["duplicate_of"] == "card_1"


def test_negative_effort_from_a_scorer_is_clamped_by_ignoring_it() -> None:
    assert (
        request(extra={"estimated_effort_hours": -5})["gate_inputs"]["estimated_effort_hours"] == 0
    )


def test_objections_are_forwarded() -> None:
    body = request(objections=[{"dimension": "competition", "objection_id": "o1"}])
    assert body["objections_accepted"] == [{"dimension": "competition", "objection_id": "o1"}]


def test_profile_for_verdict_picks_only_the_four_fields_m11_needs() -> None:
    full = {
        "user_id": "u",
        "skills": ["x"],
        "hours_per_week": 8,
        "weeks_available": 10,
        "team_size": 3,
        "compute": "laptop",
        "goal": "portfolio",
        "interests": [],
        "version": 4,
    }
    assert builders.profile_for_verdict(full) == {
        "goal": "portfolio",
        "hours_per_week": 8,
        "weeks_available": 10,
        "team_size": 3,
    }


# ---- unknowns and questions ----------------------------------------------------------------
def test_default_unknown_is_effort_with_a_half_to_double_range() -> None:
    unknowns = builders.pending_unknowns({"estimated_effort_hours": 100}, {}, [], [])
    assert len(unknowns) == 1
    spec = unknowns[0]
    assert (spec.scope, spec.field, spec.range) == (
        "gate_input",
        "estimated_effort_hours",
        (50, 200),
    )


def test_no_default_unknown_when_effort_is_known_from_the_user() -> None:
    assert (
        builders.pending_unknowns(
            {"estimated_effort_hours": 100}, {"estimated_effort_hours": 40}, [], []
        )
        == []
    )


def test_no_default_unknown_when_the_scorer_gave_no_estimate() -> None:
    assert builders.pending_unknowns({}, {}, [], []) == []


def test_caller_declared_unknowns_come_after_the_default_and_skip_overridden_ones() -> None:
    custom = UnknownSpec(
        scope="profile", field="hours_per_week", range=(5, 20), question="How many hours?"
    )
    covered = UnknownSpec(scope="gate_input", field="n_sources", range=(1, 9))
    got = builders.pending_unknowns(
        {"estimated_effort_hours": 100},
        {"n_sources": 3},
        [custom.model_dump(), covered.model_dump()],
        [],
    )
    assert [u.field for u in got] == ["estimated_effort_hours", "hours_per_week"]


def test_already_asked_unknowns_are_not_asked_again() -> None:
    got = builders.pending_unknowns(
        {"estimated_effort_hours": 100}, {}, [], ["estimated_effort_hours"]
    )
    assert got == []


def test_at_most_three_questions_are_ever_asked() -> None:
    assert builders.MAX_QUESTIONS == 3
    specs = [UnknownSpec(scope="profile", field="team_size", range=(1, 4)).model_dump()]
    asked = ["a", "b", "c"]
    assert builders.pending_unknowns({"estimated_effort_hours": 100}, {}, specs, asked) == []


def test_unknown_spec_validates_scope_field_and_range() -> None:
    with pytest.raises(ValueError):
        UnknownSpec(scope="gate_input", field="vibes", range=(1, 2))
    with pytest.raises(ValueError):
        UnknownSpec(scope="profile", field="team_size", range=(5, 1))
    with pytest.raises(ValueError):
        UnknownSpec(scope="score", field="risk", range=(0, 9))
    UnknownSpec(scope="score", field="risk", range=(0, 5))


def test_voi_request_wraps_the_verdict_request_range_and_unknown() -> None:
    spec = UnknownSpec(scope="gate_input", field="estimated_effort_hours", range=(50, 200))
    body = builders.voi_request(request(), spec)
    assert body["unknown"] == {"scope": "gate_input", "field": "estimated_effort_hours"}
    assert body["range"] == [50, 200]
    assert body["input"]["idea_id"] == "idea_1"


def test_question_payload_names_what_changes() -> None:
    spec = UnknownSpec(scope="gate_input", field="estimated_effort_hours", range=(50, 200))
    q = builders.question_payload(spec, low=("GO", 50), high=("NO-GO", 200))
    assert q["question_id"] == "q_estimated_effort_hours"
    assert q["field"] == "estimated_effort_hours"
    assert "GO" in q["why_it_matters"] and "NO-GO" in q["why_it_matters"]
    assert q["text"]


def test_custom_question_text_is_used() -> None:
    spec = UnknownSpec(
        scope="profile", field="team_size", range=(1, 4), question="How big is your team?"
    )
    q = builders.question_payload(spec, low=("GO", 1), high=("PIVOT", 4))
    assert q["text"] == "How big is your team?"


# ---- claims --------------------------------------------------------------------------------
def test_claims_exist_only_for_scores_with_evidence() -> None:
    scores = builders.parse_scores(full_raw()[:3], {E1}, RUBRICS)
    claims = builders.build_claims(scores)
    assert [c["claim_id"] for c in claims] == [
        "cl_demand_pain",
        "cl_competition",
        "cl_differentiation",
    ]
    assert claims[0]["evidence_ids"] == [E1]
    assert claims[0]["dimension"] == "demand_pain"
    assert claims[0]["text"] == "because"


def test_a_claim_text_is_never_empty() -> None:
    score = DimensionScore.model_validate({**raw_score("risk"), "reason": ""})
    assert builders.build_claims([score])[0]["text"] == "risk score 3.0"


def test_unsupported_claims_make_their_dimension_abstain() -> None:
    scores = builders.parse_scores(full_raw(), {E1}, RUBRICS)
    results = [{"claim_id": f"cl_{d}", "supported": d != "risk", "reason": "x"} for d in DIMENSIONS]
    new, removed = builders.apply_claim_results(scores, results)
    assert removed == ["cl_risk"]
    got = by_dim(new)
    assert got["risk"].score is None
    assert got["risk"].evidence_ids == []
    assert got["risk"].abstained is True
    assert got["demand_pain"].score == 3.0


def test_a_claim_with_no_result_is_treated_as_unsupported() -> None:
    scores = builders.parse_scores(full_raw(), {E1}, RUBRICS)
    results = [{"claim_id": f"cl_{d}", "supported": True, "reason": "ok"} for d in DIMENSIONS[1:]]
    _, removed = builders.apply_claim_results(scores, results)
    assert removed == ["cl_demand_pain"]


def test_all_supported_changes_nothing() -> None:
    scores = builders.parse_scores(full_raw(), {E1}, RUBRICS)
    results = [{"claim_id": f"cl_{d}", "supported": True, "reason": "ok"} for d in DIMENSIONS]
    new, removed = builders.apply_claim_results(scores, results)
    assert removed == []
    assert new == scores


def test_results_for_claims_we_never_sent_are_ignored() -> None:
    scores = builders.parse_scores(full_raw(), {E1}, RUBRICS)
    results = [{"claim_id": f"cl_{d}", "supported": True, "reason": "ok"} for d in DIMENSIONS]
    results.append({"claim_id": "cl_ghost", "supported": False, "reason": "x"})
    assert builders.apply_claim_results(scores, results)[1] == []


def test_already_abstained_dimensions_have_no_claim_to_remove() -> None:
    scores = builders.abstain_all(RUBRICS, "x")
    new, removed = builders.apply_claim_results(scores, [])
    assert removed == []
    assert new == scores
