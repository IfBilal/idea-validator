"""Validate graph extensions used by Discover, What-if and the skeptic step (doc 06 step 6-7)."""

from typing import Any

import pytest

from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import (
    DIMS,
    FakeWorld,
    Harness,
    fake_verdict,
    make_idea,
    make_objection,
    voi_response,
)

VERDICT = ("verdict", "POST /verdict")
EFFORT = "estimated_effort_hours"


async def run(h: Harness, **over: Any) -> ValidateState:
    await h.start(**over)
    return await h.state()


# ---- accepted objections reach M11 ----------------------------------------------------------------
async def test_accepted_objections_given_up_front_are_sent_to_m11() -> None:
    world = FakeWorld()
    objections = [
        {"dimension": "competition", "objection_id": "obj_1"},
        {"dimension": "risk", "objection_id": "obj_2"},
    ]
    await run(Harness(world), objections=objections)
    assert world.bodies(*VERDICT)[0]["objections_accepted"] == objections


async def test_without_objections_none_are_sent() -> None:
    world = FakeWorld()
    await run(Harness(world))
    assert world.bodies(*VERDICT)[0]["objections_accepted"] == []


# ---- non-interactive runs (Discover children) --------------------------------------------------------
async def test_a_non_interactive_run_never_asks_even_when_the_verdict_could_flip() -> None:
    world = FakeWorld()
    world.voi_fn = lambda body: voi_response(True)
    h = Harness(world)
    result = await h.start(interactive=False)
    assert "__interrupt__" not in result
    assert world.count("verdict", "POST /value-of-information") == 0
    assert (await h.state()).status is RunStatus.SUCCEEDED
    assert "question" not in h.event_types()


async def test_a_non_interactive_run_without_effort_finishes_without_a_verdict() -> None:
    world = FakeWorld()
    world.extra = {}
    h = Harness(world)
    result = await h.start(interactive=False)
    assert "__interrupt__" not in result
    state = await h.state()
    assert world.count(*VERDICT) == 0
    assert state.card is not None
    assert state.card["verdict"] is None
    assert state.card["needs_input"] == [EFFORT]


async def test_a_non_interactive_run_with_an_effort_override_gets_a_verdict() -> None:
    world = FakeWorld()
    world.extra = {}
    state = await run(Harness(world), interactive=False, overrides={EFFORT: 160})
    assert world.bodies(*VERDICT)[0]["gate_inputs"][EFFORT] == 160
    assert state.card is not None and state.card["verdict"] == "GO"


async def test_a_non_interactive_run_skips_deep_search_that_needs_approval() -> None:
    world = FakeWorld()
    world.evidence = world.evidence[:1]
    world.needs_approval = True
    h = Harness(world)
    result = await h.start(interactive=False)
    assert "__interrupt__" not in result
    state = await h.state()
    assert world.count("scouts", "POST /scout") == 0
    assert any("without approval" in n for n in state.notes)
    assert state.status is RunStatus.SUCCEEDED
    assert "approval_required" not in h.event_types()


# ---- skeptic + objection filter inside Validate (step 6) ---------------------------------------------
def structured() -> dict[str, Any]:
    return make_idea(1)


def skeptic_world(*, accept: bool = True) -> FakeWorld:
    world = FakeWorld()
    idea = structured()
    world.skeptic_fn = lambda body: {"objections": [make_objection(1, idea)]}
    if not accept:
        world.filter_fn = lambda body: {
            "accepted": [],
            "rejected": [{"objection_id": "obj_1", "reason": "quote does not support"}],
        }
    world.verdict_fn = lambda req: fake_verdict(
        req, "PIVOT" if req["objections_accepted"] else "GO"
    )
    return world


async def test_the_skeptic_runs_for_a_structured_idea_and_accepted_objections_reach_m11() -> None:
    world = skeptic_world()
    h = Harness(world)
    state = await run(h, structured_idea=structured())
    assert world.count("ideation", "POST /skeptic") == 1
    assert world.count("critic", "POST /objections/filter") == 1
    first, second = world.bodies(*VERDICT)
    assert first["objections_accepted"] == []
    assert second["objections_accepted"] == [{"dimension": "competition", "objection_id": "obj_1"}]
    assert state.card is not None
    assert state.card["verdict"] == "PIVOT"  # M11's answer to the recompute
    assert state.card["partial"] is False
    assert h.event_types().count("verdict") == 1


async def test_the_skeptic_gets_the_idea_and_the_non_null_scores() -> None:
    world = skeptic_world()
    await run(Harness(world), structured_idea=structured())
    body = world.bodies("ideation", "POST /skeptic")[0]
    assert body["ideas"][0]["idea_ref"] == structured()["idea_ref"]
    assert body["scores"] == {structured()["idea_ref"]: {d: 3.5 for d in DIMS}}


async def test_rejected_objections_cause_no_recompute() -> None:
    world = skeptic_world(accept=False)
    state = await run(Harness(world), structured_idea=structured())
    assert world.count(*VERDICT) == 1
    assert state.card is not None and state.card["verdict"] == "GO"


async def test_no_objections_from_the_skeptic_cause_no_recompute() -> None:
    world = FakeWorld()
    state = await run(Harness(world), structured_idea=structured())
    assert world.count("critic", "POST /objections/filter") == 0
    assert world.count(*VERDICT) == 1
    assert state.status is RunStatus.SUCCEEDED


async def test_a_plain_text_idea_has_no_skeptic_step() -> None:
    world = skeptic_world()
    await run(Harness(world))
    assert world.count("ideation", "POST /skeptic") == 0


async def test_a_failing_skeptic_degrades_but_keeps_the_settled_verdict() -> None:
    world = skeptic_world()
    world.faults[("ideation", "POST /skeptic")] = [503, 503, 503]
    h = Harness(world)
    state = await run(h, structured_idea=structured())
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is True
    assert any("skeptic" in n for n in state.notes)
    assert world.count(*VERDICT) == 1
    assert state.card is not None and state.card["verdict"] == "GO"
    assert world.cache == {}  # a degraded run is never cached


async def test_a_failing_objection_filter_degrades_the_same_way() -> None:
    world = skeptic_world()
    world.faults[("critic", "POST /objections/filter")] = [503, 503, 503]
    state = await run(Harness(world), structured_idea=structured())
    assert state.status is RunStatus.SUCCEEDED and state.degraded is True
    assert world.count(*VERDICT) == 1


async def test_a_budget_stop_during_the_skeptic_leaves_no_final_verdict() -> None:
    world = skeptic_world()
    h = Harness(world)

    def spend(body: dict[str, Any]) -> Any:
        h.redis.hashes.setdefault("budget:run_1", {})["steps"] = 10_000
        return {"objections": [make_objection(1, structured())]}

    world.skeptic_fn = spend
    state = await run(h, structured_idea=structured())
    assert state.status is RunStatus.BUDGET_STOPPED
    assert state.card is not None and state.card["verdict"] is None
    assert "verdict" not in h.event_types()


async def test_an_objection_from_the_skeptic_is_asked_to_the_critic_in_its_shape() -> None:
    world = skeptic_world()
    await run(Harness(world), structured_idea=structured())
    sent = world.bodies("critic", "POST /objections/filter")[0]["objections"][0]
    assert set(sent) == {"objection_id", "dimension", "text", "evidence_ids"}


# ---- suggested changes in the stability step (step 7) ---------------------------------------------------
def delta(dimension: str | None, frm: Any, to: Any, verdict: str = "GO") -> dict[str, Any]:
    return {
        "dimension": dimension,
        "from": frm,
        "to": to,
        "new_score": 71.0,
        "new_verdict": verdict,
        "stable": True,
        "change": f"raise {dimension}",
    }


def delta_world(*deltas: dict[str, Any]) -> FakeWorld:
    world = FakeWorld()
    world.verdict_fn = lambda req: fake_verdict(req, "PIVOT", what_would_make_go=list(deltas))
    return world


async def test_m11_deltas_become_suggested_changes_on_the_card() -> None:
    world = delta_world(delta("competition", 2.0, 3.5))
    state = await run(Harness(world))
    assert world.count("ideation", "POST /suggest-changes") == 1
    body = world.bodies("ideation", "POST /suggest-changes")[0]
    assert (body["target_dimension"], body["current_score"], body["to_score"]) == (
        "competition",
        2.0,
        3.5,
    )
    assert body["idea_text"].startswith("A CLI")
    assert body["evidence"]  # the evidence the dimension score cites
    assert state.card is not None
    (change,) = state.card["changes"]
    assert change["change_id"] == "chg_competition"
    assert change["text"] == "Improve competition"
    assert (change["from"], change["to"], change["new_verdict"]) == (2.0, 3.5, "GO")


async def test_at_most_three_changes_are_suggested() -> None:
    dims = ("competition", "risk", "learning", "demand_pain")
    world = delta_world(*(delta(d, 2.0, 3.0) for d in dims))
    state = await run(Harness(world))
    assert world.count("ideation", "POST /suggest-changes") == 3
    assert state.card is not None and len(state.card["changes"]) == 3


@pytest.mark.parametrize(
    "bad",
    [
        delta(None, 2.0, 3.0),  # a profile-level delta, not a dimension
        delta("competition", None, 3.0),
        delta("competition", 3.0, 3.0),  # no improvement
        delta("competition", 3.0, 2.0),
        delta("competition", 2.0, 5.1),  # above the scale
        delta("vibes", 1.0, 2.0),
    ],
)
async def test_unusable_deltas_are_skipped(bad: dict[str, Any]) -> None:
    world = delta_world(bad)
    state = await run(Harness(world))
    assert world.count("ideation", "POST /suggest-changes") == 0
    assert state.card is not None and state.card["changes"] == []


@pytest.mark.parametrize(("frm", "to"), [(0.0, 0.5), (4.5, 5.0)])
async def test_delta_boundaries_at_the_ends_of_the_scale_are_accepted(
    frm: float, to: float
) -> None:
    world = delta_world(delta("risk", frm, to))
    await run(Harness(world))
    assert world.count("ideation", "POST /suggest-changes") == 1


async def test_no_deltas_means_no_m8_call_and_an_empty_list() -> None:
    world = FakeWorld()
    state = await run(Harness(world))
    assert world.count("ideation", "POST /suggest-changes") == 0
    assert state.card is not None and state.card["changes"] == []


async def test_a_failing_change_suggester_degrades_but_keeps_the_verdict() -> None:
    world = delta_world(delta("competition", 2.0, 3.5))
    world.faults[("ideation", "POST /suggest-changes")] = [503, 503, 503]
    state = await run(Harness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is True
    assert state.card is not None
    assert state.card["verdict"] == "PIVOT"
    assert state.card["changes"] == []
    assert world.cache == {}


async def test_changes_are_not_suggested_when_there_is_no_settled_verdict() -> None:
    world = delta_world(delta("competition", 2.0, 3.5))
    world.faults[("critic", "POST /check")] = [503, 503, 503]
    await run(Harness(world))
    assert world.count("ideation", "POST /suggest-changes") == 0


# ---- the card carries the exact M11 input (for challenge) ---------------------------------------------------
async def test_a_settled_card_carries_the_m11_request_that_produced_it() -> None:
    world = FakeWorld()
    state = await run(Harness(world))
    assert state.card is not None
    assert state.card["verdict_input"] == world.bodies(*VERDICT)[-1]


async def test_a_partial_card_has_no_verdict_input() -> None:
    world = FakeWorld()
    world.faults[("critic", "POST /check")] = [503, 503, 503]
    state = await run(Harness(world))
    assert state.card is not None and "verdict_input" not in state.card


# ---- what-if entry: rewrite then rescore --------------------------------------------------------------------
def base_world(*, with_change: bool = True) -> FakeWorld:
    world = FakeWorld()
    card: dict[str, Any] = {
        "verdict": "PIVOT",
        "score": 61.0,
        "scores": [
            {"dimension": "competition", "score": 2.0},
            {"dimension": "risk", "score": 3.0},
        ],
        "changes": [{"change_id": "chg_competition", "text": "Differentiate with offline mode"}]
        if with_change
        else [],
    }
    world.versions[("idea_1", 1)] = {
        "idea_id": "idea_1",
        "version": 1,
        "user_id": "u1",
        "text": "A CLI that lints Dockerfiles",
        "card": card,
    }
    return world


WHATIF = {"base_version": 1, "change_id": "chg_competition"}


async def test_a_proposed_change_is_rewritten_saved_as_a_new_version_and_rescored() -> None:
    world = base_world()
    h = Harness(world)
    state = await run(h, idea_version=2, whatif=WHATIF)
    assert world.bodies("ideation", "POST /rewrite")[0] == {
        "run_id": "run_1",
        "idea_text": "A CLI that lints Dockerfiles",
        "change_text": "Differentiate with offline mode",
    }
    assert world.versions[("idea_1", 2)]["text"].startswith("A CLI that lints Dockerfiles (revised")
    assert world.versions[("idea_1", 2)]["card"]["version"] == 2
    assert state.idea_version == 2
    sent = world.bodies(*VERDICT)[0]
    assert sent["version"] == 2
    assert state.card is not None
    assert state.card["verdict"] == "GO"
    assert state.card["version"] == 2
    assert state.card["base_version"] == 1
    assert state.card["change_note"]


async def test_the_rescore_runs_on_the_rewritten_text() -> None:
    world = base_world()
    await run(Harness(world), idea_version=2, whatif=WHATIF)
    queries = [b["query"] for b in world.bodies("kb", "POST /search")]
    assert all("revised" in q for q in queries)
    assert "revised" in world.bodies("validators", "POST /score-all")[0]["idea"]


async def test_a_custom_change_text_is_rewritten_without_looking_up_a_change() -> None:
    world = base_world(with_change=False)
    await run(Harness(world), idea_version=2, whatif={"base_version": 1, "custom_change": "Add X"})
    assert world.bodies("ideation", "POST /rewrite")[0]["change_text"] == "Add X"


async def test_an_unknown_change_id_fails_the_run_before_any_rewrite() -> None:
    world = base_world()
    state = await run(
        Harness(world), idea_version=2, whatif={"base_version": 1, "change_id": "chg_nope"}
    )
    assert state.status is RunStatus.FAILED
    assert "REWRITE" in (state.error or "")
    assert world.count("ideation", "POST /rewrite") == 0
    assert world.count("kb", "POST /search") == 0


async def test_a_missing_base_version_fails_the_run() -> None:
    world = FakeWorld()
    state = await run(Harness(world), idea_version=2, whatif=WHATIF)
    assert state.status is RunStatus.FAILED
    assert world.count("ideation", "POST /rewrite") == 0


async def test_a_failing_rewrite_fails_the_run_without_creating_a_version() -> None:
    world = base_world()
    world.faults[("ideation", "POST /rewrite")] = [503, 503, 503]
    state = await run(Harness(world), idea_version=2, whatif=WHATIF)
    assert state.status is RunStatus.FAILED
    assert ("idea_1", 2) not in world.versions


async def test_the_new_version_is_saved_once_with_its_card_after_m11_has_answered() -> None:
    world = base_world()
    await run(Harness(world), idea_version=2, whatif=WHATIF)
    puts = [r for _, r, _ in world.calls if r.startswith("PUT /internal/ideas/")]
    assert puts == ["PUT /internal/ideas/idea_1/versions/2"]
    order = [r for _, r, _ in world.calls]
    assert order.index("POST /verdict") < order.index(puts[0])
    saved = world.versions[("idea_1", 2)]
    assert saved["idempotency_key"] == "run_1"
    assert saved["card"]["verdict"] == "GO" and saved["card"]["diff"]
    assert saved["change_note"] and saved["user_id"] == "u1"
    assert world.creates == [("idea_1", 2)]


async def test_a_newer_version_makes_the_whatif_fail_without_showing_a_verdict() -> None:
    world = base_world()
    world.versions[("idea_1", 2)] = {
        "idea_id": "idea_1",
        "version": 2,
        "user_id": "u1",
        "text": "someone else was faster",
        "card": {},
        "idempotency_key": "other-run",
    }
    h = Harness(world)
    state = await run(h, idea_version=2, whatif=WHATIF)
    assert state.status is RunStatus.FAILED
    assert "newer version" in (state.error or "")
    assert "verdict" not in h.event_types()
    assert world.cache == {}
    assert world.creates == []


async def test_a_retried_save_creates_one_version() -> None:
    world = base_world()
    world.faults[("profile", "PUT /internal/ideas/idea_1/versions/2")] = [503, 503]
    state = await run(Harness(world), idea_version=2, whatif=WHATIF)
    assert state.status is RunStatus.SUCCEEDED
    assert world.creates == [("idea_1", 2)]


async def test_the_same_whatif_run_submitted_twice_creates_one_version() -> None:
    world = base_world()
    await run(Harness(world, run_id="run_9"), idea_version=2, whatif=WHATIF)
    second = await run(Harness(world, run_id="run_9"), idea_version=2, whatif=WHATIF)
    assert second.status is RunStatus.SUCCEEDED
    assert world.creates == [("idea_1", 2)]


async def test_a_failed_whatif_scoring_writes_no_version() -> None:
    world = base_world()
    world.faults[VERDICT] = [503, 503, 503]
    state = await run(Harness(world), idea_version=2, whatif=WHATIF)
    assert state.status is RunStatus.FAILED
    assert world.creates == []


async def test_a_whatif_without_a_settled_verdict_writes_no_version() -> None:
    world = base_world()
    world.extra = {}  # no effort: the run finishes without a verdict
    state = await run(Harness(world), idea_version=2, whatif=WHATIF, interactive=False)
    assert state.card is not None and state.card["verdict"] is None
    assert world.creates == []


async def test_another_users_base_version_is_not_found_and_the_owner_is_sent() -> None:
    world = base_world()
    state = await run(Harness(world), idea_version=2, whatif=WHATIF, user_id="someone-else")
    assert state.status is RunStatus.FAILED
    gets = [q for route, q in world.queries if route.startswith("GET /internal/ideas/")]
    assert gets == [{"user_id": "someone-else"}]
    assert world.count("ideation", "POST /rewrite") == 0


async def test_the_card_has_a_diff_against_the_base_card() -> None:
    world = base_world()
    state = await run(Harness(world), idea_version=2, whatif=WHATIF)
    assert state.card is not None
    diff = state.card["diff"]
    assert (diff["verdict_from"], diff["verdict_to"]) == ("PIVOT", "GO")
    assert diff["verdict_changed"] is True
    assert (diff["score_from"], diff["score_to"]) == (61.0, 72.5)
    by_dim = {d["dimension"]: d for d in diff["dimensions"]}
    assert by_dim["competition"] == {
        "dimension": "competition",
        "from": 2.0,
        "to": 3.5,
        "delta": 1.5,
    }
    assert by_dim["risk"]["delta"] == 0.5


async def test_a_plain_validate_has_no_whatif_fields() -> None:
    state = await run(Harness(FakeWorld()))
    assert state.card is not None
    for key in ("diff", "base_version", "change_note"):
        assert key not in state.card


async def test_the_whatif_verdict_still_comes_from_m11() -> None:
    world = base_world()
    world.label = "NO-GO"
    state = await run(Harness(world), idea_version=2, whatif=WHATIF)
    assert state.card is not None and state.card["verdict"] == "NO-GO"
    assert state.card["diff"]["verdict_to"] == "NO-GO"


async def test_card_claims_carry_the_claim_text_evidence_and_dimension() -> None:
    world = FakeWorld()
    state = await run(Harness(world))
    assert state.card is not None
    sent = {c["claim_id"]: c for c in world.bodies("critic", "POST /check")[0]["claims"]}
    assert len(state.card["claims"]) == 6
    for claim in state.card["claims"]:
        original = sent[claim["claim_id"]]
        assert claim["text"] == original["text"]
        assert claim["evidence_ids"] == original["evidence_ids"]
        assert claim["dimension"] == original["dimension"]
        assert claim["supported"] is True and claim["removed"] is False
