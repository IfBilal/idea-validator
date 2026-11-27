"""Pure debate rules: drops, loop decision (hard cap 3), early stop, ranking, child inputs."""

from typing import Any

import pytest

from svc_orchestrator.models.ideas import Idea, Objection
from svc_orchestrator.services import debate
from svc_orchestrator.testing import make_idea, make_objection


def idea(n: int, **over: Any) -> Idea:
    return Idea.model_validate(make_idea(n, **over))


def obj(n: int, ref_idea: Idea, **over: Any) -> Objection:
    return Objection.model_validate(make_objection(n, ref_idea.model_dump(), **over))


# ---- gap evidence --------------------------------------------------------------------------
def test_an_idea_with_complaints_or_missing_features_keeps_its_gap_evidence() -> None:
    finding = {"text": "slow", "evidence_ids": ["card_00000001#c0"]}
    a = idea(1, complaints=[finding])
    b = idea(2, missing_features=[finding])
    kept, dropped = debate.require_gap_evidence([a, b], round_=1)
    assert [i.idea_ref for i in kept] == [a.idea_ref, b.idea_ref]
    assert dropped == []


def test_an_idea_with_no_findings_is_dropped_for_lacking_gap_evidence() -> None:
    bare = idea(1)
    kept, dropped = debate.require_gap_evidence([bare], round_=2)
    assert kept == []
    assert dropped == [
        {
            "idea_ref": bare.idea_ref,
            "title": bare.title,
            "round": 2,
            "reason": "no_gap_evidence",
            "defeated_by_evidence_ids": [],
        }
    ]


def test_an_idea_with_no_gap_evidence_ids_is_dropped() -> None:
    finding = {"text": "slow", "evidence_ids": ["card_00000001#c0"]}
    weak = idea(1, gap_evidence_ids=[], complaints=[finding])
    kept, dropped = debate.require_gap_evidence([weak], round_=1)
    assert kept == [] and dropped[0]["reason"] == "no_gap_evidence"


# ---- drop rules ----------------------------------------------------------------------------
def test_no_objections_drops_nothing() -> None:
    a = idea(1)
    out = debate.apply_objections([a], [], round_=1)
    assert out.dropped == []
    assert [i.idea_ref for i in out.survivors] == [a.idea_ref]
    assert out.nonfatal == []


def test_one_fatal_accepted_objection_drops_the_idea_with_its_evidence() -> None:
    a = idea(1)
    fatal = obj(1, a, severity="fatal", dimension="feasibility")
    out = debate.apply_objections([a], [fatal], round_=2)
    assert out.survivors == []
    (dropped,) = out.dropped
    assert dropped["reason"] == "fatal_objection"
    assert dropped["round"] == 2
    assert dropped["defeated_by_evidence_ids"] == fatal.evidence_ids
    assert dropped["title"] == a.title


def test_two_accepted_objections_on_different_dimensions_drop_the_idea() -> None:
    a = idea(1)
    objections = [obj(1, a, dimension="competition"), obj(2, a, dimension="risk")]
    out = debate.apply_objections([a], objections, round_=1)
    assert out.survivors == []
    assert out.dropped[0]["reason"] == "multiple_objections"
    assert out.dropped[0]["defeated_by_evidence_ids"] == sorted(
        {e for o in objections for e in o.evidence_ids}
    )


def test_two_accepted_objections_on_the_same_dimension_do_not_drop_it() -> None:
    a = idea(1)
    objections = [obj(1, a, dimension="competition"), obj(2, a, dimension="competition")]
    out = debate.apply_objections([a], objections, round_=1)
    assert [i.idea_ref for i in out.survivors] == [a.idea_ref]
    assert [o.objection_id for o in out.nonfatal] == ["obj_1", "obj_2"]


def test_one_non_fatal_objection_keeps_the_idea_and_is_passed_on() -> None:
    a = idea(1)
    minor = obj(1, a, severity="minor")
    out = debate.apply_objections([a], [minor], round_=1)
    assert [i.idea_ref for i in out.survivors] == [a.idea_ref]
    assert out.nonfatal == [minor]


def test_objections_for_other_ideas_do_not_affect_an_idea() -> None:
    a, b = idea(1), idea(2)
    out = debate.apply_objections([a, b], [obj(1, b, severity="fatal")], round_=1)
    assert [i.idea_ref for i in out.survivors] == [a.idea_ref]
    assert [d["idea_ref"] for d in out.dropped] == [b.idea_ref]


def test_a_dropped_ideas_other_objections_are_not_passed_on() -> None:
    a = idea(1)
    out = debate.apply_objections(
        [a], [obj(1, a, severity="fatal"), obj(2, a, severity="minor")], round_=1
    )
    assert out.nonfatal == []


def test_objections_naming_unknown_ideas_are_ignored() -> None:
    a = idea(1)
    stray = Objection.model_validate(make_objection(9, {"idea_ref": "idea_nobody"}))
    out = debate.apply_objections([a], [stray], round_=1)
    assert [i.idea_ref for i in out.survivors] == [a.idea_ref]
    assert out.nonfatal == []


# ---- loop decision: hard cap 3, early stops --------------------------------------------------
@pytest.mark.parametrize(
    ("round_", "expected"), [(1, True), (2, True), (3, False), (4, False), (99, False)]
)
def test_the_debate_never_goes_past_round_three(round_: int, expected: bool) -> None:
    assert debate.should_continue(round_, 3, new_nonfatal=2, survivors=1, requested=5) is expected


@pytest.mark.parametrize("configured", [4, 10, 100])
def test_a_configured_cap_above_three_is_clamped_in_code(configured: int) -> None:
    assert debate.MAX_ROUNDS == 3
    assert not debate.should_continue(3, configured, new_nonfatal=2, survivors=1, requested=5)
    assert debate.should_continue(2, configured, new_nonfatal=2, survivors=1, requested=5)


def test_a_lower_configured_cap_is_respected() -> None:
    assert not debate.should_continue(1, 1, new_nonfatal=2, survivors=1, requested=5)
    assert debate.should_continue(1, 2, new_nonfatal=2, survivors=1, requested=5)


def test_early_stop_when_there_are_no_new_non_fatal_objections() -> None:
    assert not debate.should_continue(1, 3, new_nonfatal=0, survivors=1, requested=5)


@pytest.mark.parametrize(("survivors", "expected"), [(3, True), (4, True), (5, False), (6, False)])
def test_early_stop_when_there_are_enough_survivors(survivors: int, expected: bool) -> None:
    assert (
        debate.should_continue(1, 3, new_nonfatal=1, survivors=survivors, requested=5) is expected
    )


def test_only_unseen_objections_count_as_new() -> None:
    a = idea(1)
    objections = [obj(1, a), obj(2, a, dimension="risk")]
    assert [o.objection_id for o in debate.unseen(objections, {"obj_1"})] == ["obj_2"]
    assert debate.unseen(objections, {"obj_1", "obj_2"}) == []


# ---- how many ideas to ask for -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("round_", "requested", "survivors", "expected"),
    [(1, 5, 0, 8), (1, 10, 0, 13), (1, 1, 0, 4), (2, 5, 2, 6), (2, 5, 8, 1), (3, 10, 0, 13)],
)
def test_ideation_count_over_generates_and_never_asks_for_less_than_one(
    round_: int, requested: int, survivors: int, expected: int
) -> None:
    assert debate.ideation_count(round_, requested, survivors) == expected


def test_ideation_count_never_exceeds_the_m8_limit_of_twenty() -> None:
    assert debate.ideation_count(1, 20, 0) == 20


# ---- ranking (A-19) ------------------------------------------------------------------------------
def item(verdict: str | None, score: float | None, conf: float | None, tag: str) -> dict[str, Any]:
    return {"verdict": verdict, "score": score, "confidence": conf, "tag": tag}


def test_ranking_orders_by_verdict_rank_then_score_then_confidence() -> None:
    items = [
        item("NO-GO", 90, 0.9, "nogo"),
        item("GO", 71, 0.6, "go_low"),
        item("INSUFFICIENT_EVIDENCE", None, 0.2, "ie"),
        item("PIVOT", 65, 0.8, "pivot"),
        item("GO", 71, 0.9, "go_conf"),
        item("GO", 80, 0.5, "go_high"),
    ]
    ranked = debate.rank(items)
    assert [i["tag"] for i in ranked] == ["go_high", "go_conf", "go_low", "pivot", "ie", "nogo"]


def test_a_card_without_a_verdict_ranks_last() -> None:
    ranked = debate.rank([item(None, None, None, "none"), item("NO-GO", 10, 0.1, "nogo")])
    assert [i["tag"] for i in ranked] == ["nogo", "none"]


def test_missing_scores_rank_below_present_scores_within_a_verdict() -> None:
    ranked = debate.rank([item("GO", None, 0.9, "a"), item("GO", 0, 0.1, "b")])
    assert [i["tag"] for i in ranked] == ["b", "a"]


def test_ties_keep_the_original_order() -> None:
    ranked = debate.rank([item("GO", 70, 0.5, "first"), item("GO", 70, 0.5, "second")])
    assert [i["tag"] for i in ranked] == ["first", "second"]


def test_ranking_does_not_change_any_item() -> None:
    items = [item("GO", 70, 0.5, "x")]
    assert debate.rank(items) == items
    assert debate.rank([]) == []


def test_an_unknown_verdict_label_ranks_with_no_verdict() -> None:
    assert debate.verdict_rank("MAYBE") == debate.verdict_rank(None) == 0
    assert [debate.verdict_rank(v) for v in ("GO", "PIVOT", "INSUFFICIENT_EVIDENCE", "NO-GO")] == [
        4,
        3,
        2,
        1,
    ]


# ---- child validation inputs ---------------------------------------------------------------------
@pytest.mark.parametrize(("scope", "hours"), [("S", 60), ("M", 160), ("L", 400)])
def test_scope_maps_to_the_documented_effort_hours(scope: str, hours: int) -> None:
    assert debate.scope_hours(scope) == hours


def test_scope_hours_rejects_unknown_scopes() -> None:
    with pytest.raises(ValueError):
        debate.scope_hours("XL")


def test_idea_text_is_title_summary_user_and_features_and_within_the_limit() -> None:
    a = idea(1, summary="x" * 5000 if False else "Lints Dockerfiles")
    text = debate.idea_text(a)
    assert a.title in text and "Lints Dockerfiles" in text and "students" in text
    assert "a; b; c" in text
    assert 0 < len(text) <= 4000


def test_a_very_long_idea_text_is_truncated_to_4000() -> None:
    a = idea(1, core_features=["f" * 200] * 6, title="t" * 200, target_user="u" * 200)
    assert len(debate.idea_text(a)) <= 4000


def test_accepted_objections_become_m11_objections() -> None:
    a = idea(1)
    out = debate.m11_objections([obj(1, a), obj(2, a, dimension="risk")])
    assert out == [
        {"dimension": "competition", "objection_id": "obj_1"},
        {"dimension": "risk", "objection_id": "obj_2"},
    ]


def test_result_item_has_the_documented_shape_and_forwards_the_verdict() -> None:
    a = idea(1)
    card = {
        "verdict": "PIVOT",
        "stability": "BORDERLINE",
        "score": 61.5,
        "confidence": 0.7,
        "reasons": ["weak differentiation"],
        "partial": False,
        "needs_input": [],
    }
    got = debate.result_item(a, card, version=1)
    assert got == {
        "idea_id": a.idea_ref,
        "version": 1,
        "title": a.title,
        "summary": a.summary,
        "niche": "devtools",
        "verdict": "PIVOT",
        "stability": "BORDERLINE",
        "score": 61.5,
        "confidence": 0.7,
        "reason": "weak differentiation",
        "gap_evidence_ids": a.gap_evidence_ids,
        "partial": False,
        "needs_input": [],
    }


def test_result_item_for_a_partial_card_has_no_verdict() -> None:
    a = idea(1)
    got = debate.result_item(a, {"verdict": None, "partial": True, "stop_reason": "budget"}, 1)
    assert got["verdict"] is None
    assert got["score"] is None
    assert got["partial"] is True
    assert got["reason"] == "budget"
