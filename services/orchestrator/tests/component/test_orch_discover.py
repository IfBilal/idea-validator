"""Behaviour tests: the Discover graph against fake modules (M2, M7, M8, M9, M11, M12, ...)."""

import asyncio
from typing import Any

import pytest

from common.budget import Spend
from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import (
    DiscoverHarness,
    FakeWorld,
    fake_verdict,
    make_idea,
    make_objection,
)

IDEATE = ("ideation", "POST /ideate")
SKEPTIC = ("ideation", "POST /skeptic")
FILTER = ("critic", "POST /objections/filter")
VERDICT = ("verdict", "POST /verdict")


def ref(n: int) -> str:
    return str(make_idea(n)["idea_ref"])


def n_of(idea_ref: str) -> int:
    return int(idea_ref.removeprefix("idea_"), 16)


async def run(h: DiscoverHarness, **over: Any) -> DiscoverState:
    return await h.start(**over)


def objecting(
    *specs: tuple[int, int, dict[str, Any]],
) -> Any:
    """A skeptic that raises objection ``(obj number, idea number, overrides)`` every round."""

    def skeptic(body: dict[str, Any]) -> Any:
        out = []
        present = {i["idea_ref"] for i in body["ideas"]}
        for number, idea_n, over in specs:
            if ref(idea_n) in present:
                out.append(make_objection(number, make_idea(idea_n), **over))
        return {"objections": out}

    return skeptic


def few_ideas(per_round: int = 2) -> Any:
    """An ideator that returns ``per_round`` new ideas each round (so survivors stay few)."""

    def ideate(body: dict[str, Any]) -> Any:
        first = (body["round"] - 1) * per_round + 1
        ideas = [make_idea(n) for n in range(first, first + per_round)]
        return {"ideas": ideas, "dropped": [], "reason": None, "round": body["round"]}

    return ideate


# ---- happy path ----------------------------------------------------------------------------------
async def test_a_clean_discover_validates_the_top_survivors_and_ranks_them() -> None:
    world = FakeWorld()
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    ideas = state.card["ideas"]
    assert len(ideas) == 5  # asked for 5; 8 were generated, 5 are validated
    assert all(i["verdict"] == "GO" for i in ideas)
    assert state.card["dropped"] == []
    assert state.card["partial"] is False
    assert world.bodies(*IDEATE)[0]["count"] == 8  # count + 3
    assert world.count(*VERDICT) == 5


async def test_the_ideator_gets_the_profile_niches_and_gaps_in_m8_shape() -> None:
    world = FakeWorld()
    await run(DiscoverHarness(world), niche="devtools")
    assert world.bodies("analyst", "POST /niches/match")[0]["niche_wish"] == "devtools"
    body = world.bodies(*IDEATE)[0]
    assert body["niches"] == [{"name": "devtools", "status": "rising"}]
    assert body["gaps"] == [
        {
            "gap_id": "gap_1",
            "text": "slow exports",
            "niche": "devtools",
            "evidence_ids": ["card_00000001#c0"],
        }
    ]
    assert body["profile"]["goal"] == "startup"
    assert (body["round"], body["previous_objections"]) == (1, [])


async def test_each_result_item_has_the_documented_shape() -> None:
    state = await run(DiscoverHarness(FakeWorld()), count=1)
    assert state.card is not None
    (item,) = state.card["ideas"]
    assert set(item) == {
        "idea_id",
        "version",
        "title",
        "summary",
        "niche",
        "verdict",
        "stability",
        "score",
        "confidence",
        "reason",
        "gap_evidence_ids",
        "partial",
        "needs_input",
    }
    assert item["version"] == 1
    assert item["idea_id"].startswith("idea_")


async def test_the_verdicts_in_the_result_are_exactly_what_m11_returned() -> None:
    world = FakeWorld()
    world.verdict_fn = lambda req: fake_verdict(req, "PIVOT", score=61.25, confidence=0.55)
    state = await run(DiscoverHarness(world), count=2)
    assert state.card is not None
    for item in state.card["ideas"]:
        assert (item["verdict"], item["score"], item["confidence"]) == ("PIVOT", 61.25, 0.55)


async def test_ranking_is_verdict_then_score_then_confidence() -> None:
    labels = {
        1: ("NO-GO", 90.0, 0.9),
        2: ("GO", 71.0, 0.6),
        3: ("PIVOT", 65.0, 0.9),
        4: ("GO", 80.0, 0.5),
    }
    world = FakeWorld()

    def engine(req: dict[str, Any]) -> Any:
        label, score, conf = labels[n_of(req["idea_id"])]
        return fake_verdict(req, label, score=score, confidence=conf)

    world.verdict_fn = engine
    world.ideate_fn = lambda body: {
        "ideas": [make_idea(n) for n in labels],
        "dropped": [],
        "reason": None,
        "round": 1,
    }
    state = await run(DiscoverHarness(world), count=4)
    assert state.card is not None
    assert [n_of(i["idea_id"]) for i in state.card["ideas"]] == [4, 2, 3, 1]


async def test_partial_results_are_streamed_one_per_validated_idea() -> None:
    h = DiscoverHarness(FakeWorld())
    await run(h, count=3)
    events = h.events("partial_result")
    assert len(events) == 3
    assert set(events[0].payload) == {"idea_id", "verdict", "score"}


async def test_children_do_not_emit_their_own_lifecycle_events() -> None:
    h = DiscoverHarness(FakeWorld())
    await run(h, count=2)
    types = h.event_types()
    assert types.count("run_completed") == 1
    assert types[-1] == "run_completed"
    assert "verdict" not in types
    assert "question" not in types
    seqs = [e.seq for e in h.publisher.events_of("run_1")]
    assert seqs == list(range(1, len(seqs) + 1))


# ---- debate loop ---------------------------------------------------------------------------------------
async def test_the_debate_runs_at_most_three_rounds_even_if_objections_keep_coming() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    counter = {"n": 0}

    def skeptic(body: dict[str, Any]) -> Any:
        counter["n"] += 1  # a brand new minor objection every round
        idea = body["ideas"][0]
        return {
            "objections": [
                make_objection(100 + counter["n"], idea, severity="minor", dimension="learning")
            ]
        }

    world.skeptic_fn = skeptic
    h = DiscoverHarness(world)
    state = await run(h)
    rounds = [b["round"] for b in world.bodies(*IDEATE)]
    assert rounds == [1, 2, 3]
    assert state.rounds_run == 3
    assert world.count(*SKEPTIC) == 3
    assert state.status is RunStatus.SUCCEEDED


async def test_objections_from_a_round_are_handed_to_the_next_ideate_call() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    world.skeptic_fn = objecting((1, 1, {"severity": "minor", "dimension": "learning"}))
    await run(DiscoverHarness(world))
    second = world.bodies(*IDEATE)[1]
    assert [o["objection_id"] for o in second["previous_objections"]] == ["obj_1"]
    assert second["round"] == 2


async def test_the_debate_stops_early_when_no_new_objections_are_accepted() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    # the same objection id every round: nothing new after round 1
    world.skeptic_fn = objecting((1, 1, {"severity": "minor", "dimension": "learning"}))
    h = DiscoverHarness(world)
    await run(h)
    assert [b["round"] for b in world.bodies(*IDEATE)] == [1, 2]


async def test_the_debate_stops_when_there_are_no_objections_at_all() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    await run(DiscoverHarness(world))
    assert world.count(*IDEATE) == 1
    assert world.count(*FILTER) == 0


async def test_the_debate_stops_when_there_are_enough_survivors() -> None:
    world = FakeWorld()  # 8 ideas, 8 survivors >= count 5
    world.skeptic_fn = objecting((1, 1, {"severity": "minor", "dimension": "learning"}))
    await run(DiscoverHarness(world))
    assert world.count(*IDEATE) == 1


async def test_a_lower_configured_round_cap_is_respected() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    counter = {"n": 0}

    def skeptic(body: dict[str, Any]) -> Any:
        counter["n"] += 1
        return {
            "objections": [
                make_objection(
                    200 + counter["n"], body["ideas"][0], severity="minor", dimension="learning"
                )
            ]
        }

    world.skeptic_fn = skeptic
    await run(DiscoverHarness(world, debate_max_rounds=2))
    assert [b["round"] for b in world.bodies(*IDEATE)] == [1, 2]


async def test_later_rounds_ask_only_for_what_is_missing() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    world.skeptic_fn = objecting((1, 1, {"severity": "minor", "dimension": "learning"}))
    await run(
        DiscoverHarness(
            world,
        )
    )
    bodies = world.bodies(*IDEATE)
    assert bodies[0]["count"] == 8
    assert bodies[1]["count"] == 6  # 5 + 3 - 2 survivors


# ---- drop rules ---------------------------------------------------------------------------------------------
async def test_a_fatal_objection_backed_by_evidence_drops_the_idea_with_an_event() -> None:
    world = FakeWorld()
    world.skeptic_fn = objecting((7, 1, {"severity": "fatal", "dimension": "risk"}))
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    (dropped,) = state.card["dropped"]
    assert dropped["idea_id"] == ref(1)
    assert dropped["reason"] == "fatal_objection"
    assert dropped["round"] == 1
    assert dropped["defeated_by_evidence_ids"] == ["card_00000007#c0"]
    event = h.events("idea_dropped")[0]
    assert event.payload == {
        "title": "Idea number 1",
        "round": 1,
        "reason": "fatal_objection",
        "defeated_by_evidence_ids": ["card_00000007#c0"],
    }
    validated = {n_of(b["idea_id"]) for b in world.bodies(*VERDICT)}
    assert 1 not in validated


async def test_two_objections_on_different_dimensions_drop_an_idea() -> None:
    world = FakeWorld()
    world.skeptic_fn = objecting(
        (1, 2, {"dimension": "competition"}), (2, 2, {"dimension": "feasibility"})
    )
    state = await run(DiscoverHarness(world))
    assert state.card is not None
    assert [d["reason"] for d in state.card["dropped"]] == ["multiple_objections"]


async def test_an_objection_the_critic_rejects_defeats_nothing() -> None:
    world = FakeWorld()
    world.skeptic_fn = objecting((7, 1, {"severity": "fatal", "dimension": "risk"}))
    world.filter_fn = lambda body: {
        "accepted": [],
        "rejected": [{"objection_id": "obj_7", "reason": "quote does not support"}],
    }
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    assert state.card["dropped"] == []
    assert h.events("idea_dropped") == []
    assert 1 in {n_of(b["idea_id"]) for b in world.bodies(*VERDICT)}


async def test_an_idea_without_gap_evidence_is_dropped_before_the_skeptic() -> None:
    world = FakeWorld()

    def analyst(body: dict[str, Any]) -> Any:
        ideas = [
            {
                **i,
                "complaints": []
                if n_of(i["idea_ref"]) == 2
                else [{"text": "slow", "evidence_ids": i["gap_evidence_ids"]}],
            }
            for i in body["ideas"]
        ]
        return {"ideas": ideas}

    world.gap_fn = analyst
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    (dropped,) = state.card["dropped"]
    assert (dropped["idea_id"], dropped["reason"]) == (ref(2), "no_gap_evidence")
    sent = world.bodies(*SKEPTIC)[0]["ideas"]
    assert ref(2) not in {i["idea_ref"] for i in sent}


async def test_an_idea_the_gap_analyst_did_not_return_is_dropped() -> None:
    world = FakeWorld()
    original = world.gap_fn

    def analyst(body: dict[str, Any]) -> Any:
        ideas = [
            {**i, "complaints": [{"text": "c", "evidence_ids": i["gap_evidence_ids"]}]}
            for i in body["ideas"]
            if n_of(i["idea_ref"]) != 3
        ]
        return {"ideas": ideas}

    world.gap_fn = analyst
    assert original is None
    state = await run(DiscoverHarness(world))
    assert state.card is not None
    assert [d["idea_id"] for d in state.card["dropped"]] == [ref(3)]


async def test_accepted_non_fatal_objections_reach_m11_for_that_idea_only() -> None:
    world = FakeWorld()
    world.skeptic_fn = objecting((5, 1, {"severity": "minor", "dimension": "learning"}))
    world.ideate_fn = few_ideas(3)
    await run(DiscoverHarness(world), count=3)
    by_idea = {n_of(b["idea_id"]): b["objections_accepted"] for b in world.bodies(*VERDICT)}
    assert by_idea[1] == [{"dimension": "learning", "objection_id": "obj_5"}]
    assert by_idea[2] == []


async def test_a_dropped_idea_is_never_reintroduced_by_a_later_round() -> None:
    world = FakeWorld()
    world.ideate_fn = few_ideas()
    world.skeptic_fn = objecting(
        (1, 1, {"severity": "fatal", "dimension": "risk"}),
        (2, 2, {"severity": "minor", "dimension": "learning"}),
    )
    state = await run(DiscoverHarness(world))
    assert state.card is not None
    assert ref(1) not in {i["idea_id"] for i in state.card["ideas"]}


# ---- failure paths ----------------------------------------------------------------------------------------------
async def test_no_gaps_means_an_empty_result_with_a_note_and_no_ideation() -> None:
    world = FakeWorld()
    world.gaps_fn = lambda: {"gaps": []}
    state = await run(DiscoverHarness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["ideas"] == []
    assert any("no gaps" in n for n in state.card["notes"])
    assert world.count(*IDEATE) == 0


async def test_the_ideator_returning_nothing_gives_an_empty_result_with_the_reason() -> None:
    world = FakeWorld()
    world.ideate_fn = lambda body: {
        "ideas": [],
        "dropped": [],
        "reason": "model returned invalid JSON",
        "round": 1,
    }
    state = await run(DiscoverHarness(world))
    assert state.card is not None
    assert state.card["ideas"] == []
    assert any("invalid JSON" in n for n in state.card["notes"])
    assert state.status is RunStatus.SUCCEEDED


async def test_when_m7_is_down_the_run_fails_with_an_empty_partial_result() -> None:
    world = FakeWorld()
    world.faults[("analyst", "POST /niches/match")] = [503] * 3
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.status is RunStatus.FAILED
    assert "NICHES" in (state.error or "")
    assert state.card is not None
    assert state.card["ideas"] == [] and state.card["partial"] is True
    assert h.event_types()[-1] == "run_failed"


async def test_when_the_ideator_is_down_the_run_fails() -> None:
    world = FakeWorld()
    world.faults[IDEATE] = [503] * 3
    state = await run(DiscoverHarness(world))
    assert state.status is RunStatus.FAILED
    assert world.count(*VERDICT) == 0


async def test_when_the_gap_analyst_is_down_the_run_fails() -> None:
    world = FakeWorld()
    world.faults[("ideation", "POST /gap-analyst")] = [503] * 3
    state = await run(DiscoverHarness(world))
    assert state.status is RunStatus.FAILED


async def test_a_failing_skeptic_degrades_and_the_ideas_continue_unchallenged() -> None:
    world = FakeWorld()
    world.faults[SKEPTIC] = [503] * 3
    state = await run(DiscoverHarness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["degraded"] is True
    assert len(state.card["ideas"]) == 5
    assert any("skeptic failed" in n for n in state.card["notes"])


async def test_a_failing_objection_filter_never_drops_an_idea_on_unverified_objections() -> None:
    world = FakeWorld()
    world.skeptic_fn = objecting((7, 1, {"severity": "fatal", "dimension": "risk"}))
    world.faults[FILTER] = [503] * 3
    state = await run(DiscoverHarness(world))
    assert state.card is not None
    assert state.card["dropped"] == []
    assert state.card["degraded"] is True


async def test_unknown_user_profile_fails_the_run() -> None:
    world = FakeWorld()
    world.faults[("profile", "GET /internal/profiles/u1")] = [404]
    state = await run(DiscoverHarness(world))
    assert state.status is RunStatus.FAILED
    assert "REQUEST" in (state.error or "")


async def test_a_failing_child_is_a_partial_item_and_the_other_children_still_finish() -> None:
    world = FakeWorld()
    world.faults[VERDICT] = [503] * 3  # the first child to reach M11 fails for good
    state = await run(DiscoverHarness(world, children_concurrency=1), count=3)
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    verdicts = sorted(str(i["verdict"]) for i in state.card["ideas"])
    assert verdicts == ["GO", "GO", "None"]
    broken = next(i for i in state.card["ideas"] if i["verdict"] is None)
    assert broken["partial"] is True
    assert state.card["ideas"][-1] is broken  # no verdict ranks last


async def test_a_crashing_child_does_not_lose_the_others() -> None:
    world = FakeWorld()
    calls = {"n": 0}

    async def hook(base: Any, state: ValidateState, thread: str) -> ValidateState:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom /secret/path")
        done: ValidateState = await base(state, thread)
        return done

    state = await run(DiscoverHarness(world, child_hook=hook), count=3)
    assert state.card is not None
    assert state.status is RunStatus.SUCCEEDED
    broken = [i for i in state.card["ideas"] if i["verdict"] is None]
    assert len(broken) == 1 and "boom" not in str(broken)
    assert broken[0]["reason"] == "internal error"


# ---- children: concurrency, unattended, effort from the scope --------------------------------------------------
@pytest.mark.parametrize(("limit", "expected"), [(1, 1), (3, 3), (10, 5)])
async def test_children_run_at_most_the_configured_number_at_once(
    limit: int, expected: int
) -> None:
    live = {"now": 0, "max": 0}

    async def hook(base: Any, state: ValidateState, thread: str) -> ValidateState:
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        await asyncio.sleep(0.02)
        try:
            done: ValidateState = await base(state, thread)
            return done
        finally:
            live["now"] -= 1

    await run(DiscoverHarness(FakeWorld(), child_hook=hook, children_concurrency=limit), count=5)
    assert live["max"] == expected


async def test_children_never_ask_questions_even_when_m11_says_the_answer_matters() -> None:
    from svc_orchestrator.testing import voi_response

    world = FakeWorld()
    world.voi_fn = lambda body: voi_response(True)
    h = DiscoverHarness(world)
    state = await run(h, count=2)
    assert world.count("verdict", "POST /value-of-information") == 0
    assert state.status is RunStatus.SUCCEEDED
    assert "question" not in h.event_types()


@pytest.mark.parametrize(("scope", "hours"), [("S", 60), ("M", 160), ("L", 400)])
async def test_the_ideators_scope_gives_the_documented_effort(scope: str, hours: int) -> None:
    world = FakeWorld()
    world.ideate_fn = lambda body: {
        "ideas": [make_idea(1, est_scope=scope)],
        "dropped": [],
        "reason": None,
        "round": 1,
    }
    await run(DiscoverHarness(world), count=1)
    assert world.bodies(*VERDICT)[0]["gate_inputs"]["estimated_effort_hours"] == hours


async def test_a_child_is_validated_as_version_one_of_its_own_idea() -> None:
    world = FakeWorld()
    await run(DiscoverHarness(world), count=1)
    body = world.bodies(*VERDICT)[0]
    assert body["version"] == 1
    assert body["idea_id"].startswith("idea_")
    assert body["run_id"] == "run_1"


async def test_children_share_the_parent_budget() -> None:
    h = DiscoverHarness(FakeWorld())
    await run(h, count=3)
    spent = await h.ledger.spent()
    assert spent.steps > 3 * 10  # each child's steps landed in the one run ledger


async def test_survivors_with_fewer_objections_are_validated_first() -> None:
    world = FakeWorld()
    world.skeptic_fn = objecting((1, 1, {"severity": "minor", "dimension": "learning"}))
    await run(DiscoverHarness(world), count=3)
    validated = {n_of(b["idea_id"]) for b in world.bodies(*VERDICT)}
    assert 1 not in validated and len(validated) == 3


# ---- budgets -------------------------------------------------------------------------------------------------------
async def test_an_exhausted_budget_stops_the_run_with_the_partial_result() -> None:
    h = DiscoverHarness(FakeWorld(), limits={"max_steps": 40})
    state = await run(h, count=5)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert state.card is not None
    assert state.card["partial"] is True
    assert state.card["stop_reason"]
    assert h.event_types()[-1] == "budget_stopped"
    assert "run_completed" not in h.event_types()


async def test_a_run_already_over_budget_makes_no_module_calls() -> None:
    h = DiscoverHarness(FakeWorld())
    await h.ledger.record(Spend(steps=10_000))
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert h.world.calls == []


async def test_a_budget_stop_before_the_children_leaves_the_debate_result() -> None:
    world = FakeWorld()
    h = DiscoverHarness(world)

    def spend(body: dict[str, Any]) -> Any:
        h.redis.hashes.setdefault("budget:run_1", {})["steps"] = 10_000
        return {"objections": []}

    world.skeptic_fn = spend
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert world.count(*VERDICT) == 0


# ---- tracing -------------------------------------------------------------------------------------------------------------
async def test_every_module_call_is_traced_and_no_idea_text_is_in_the_inputs() -> None:
    h = DiscoverHarness(FakeWorld())
    await run(h, count=1)
    tools = {r.tool for r in h.records}
    assert {
        "m2.get_profile",
        "m7.match_niches",
        "m7.gaps",
        "m8.ideate",
        "m8.gap_analyst",
        "m8.skeptic",
    } <= tools
    assert {r.run_id for r in h.records} == {"run_1"}
    assert "Idea number" not in "".join(str(r.input) for r in h.records)


async def test_the_debate_rounds_are_traced_as_decisions() -> None:
    h = DiscoverHarness(FakeWorld())
    await run(h)
    rounds = [
        r
        for r in h.records
        if isinstance(r.output, dict) and r.output.get("decision") == "debate_round"
    ]
    assert len(rounds) == 1
    assert rounds[0].output["continue"] is False
