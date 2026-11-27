"""Behaviour: Discover drops an idea the debate defeated (docs/harness/07, A-18/A-20).

A fatal objection that the critic accepted (evidence-backed) removes the idea: it is absent
from the results and never validated, the event idea_dropped says why, and an idea proposed
again in a later round stays out.
"""

from typing import Any

import pytest

from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.testing import DiscoverHarness, FakeWorld, make_idea, make_objection

IDEATE = ("ideation", "POST /ideate")
FILTER = ("critic", "POST /objections/filter")
VERDICT = ("verdict", "POST /verdict")


def ref(n: int) -> str:
    return str(make_idea(n)["idea_ref"])


def validated(world: FakeWorld) -> set[str]:
    return {b["idea_id"] for b in world.bodies(*VERDICT)}


def skeptic_raising(*specs: tuple[int, int, dict[str, Any]]) -> Any:
    def skeptic(body: dict[str, Any]) -> Any:
        present = {i["idea_ref"] for i in body["ideas"]}
        return {
            "objections": [
                make_objection(number, make_idea(idea_n), **over)
                for number, idea_n, over in specs
                if ref(idea_n) in present
            ]
        }

    return skeptic


def ideator_repeating_idea_one() -> Any:
    def ideate(body: dict[str, Any]) -> Any:
        numbers = (1, 2) if body["round"] == 1 else (1, 3)  # idea 1 comes back in round 2
        return {
            "ideas": [make_idea(n) for n in numbers],
            "dropped": [],
            "reason": None,
            "round": body["round"],
        }

    return ideate


async def run(h: DiscoverHarness, **over: Any) -> DiscoverState:
    return await h.start(**over)


FATAL = {"severity": "fatal", "dimension": "risk"}


async def test_a_fatal_objection_drops_the_idea_from_results_validation_and_the_card() -> None:
    world = FakeWorld()
    world.skeptic_fn = skeptic_raising((7, 1, FATAL))
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    assert ref(1) not in {i["idea_id"] for i in state.card["ideas"]}
    assert ref(1) not in validated(world)
    assert [d["idea_id"] for d in state.card["dropped"]] == [ref(1)]
    assert state.card["dropped"][0]["reason"] == "fatal_objection"
    assert ref(2) in {i["idea_id"] for i in state.card["ideas"]}  # the others carry on


async def test_the_idea_dropped_event_says_what_was_dropped_why_and_on_what_evidence() -> None:
    world = FakeWorld()
    world.skeptic_fn = skeptic_raising((7, 1, FATAL))
    h = DiscoverHarness(world)
    await run(h)
    (event,) = h.events("idea_dropped")
    assert event.payload == {
        "title": "Idea number 1",
        "round": 1,
        "reason": "fatal_objection",
        "defeated_by_evidence_ids": ["card_00000007#c0"],
    }
    assert h.event_types().index("idea_dropped") < h.event_types().index("partial_result")


async def test_the_drop_is_traced_as_a_decision_of_the_debate_round() -> None:
    world = FakeWorld()
    world.skeptic_fn = skeptic_raising((7, 1, FATAL))
    h = DiscoverHarness(world)
    await run(h)
    rounds = [
        r for r in h.records if r.kind == "decision" and r.output.get("decision") == "debate_round"
    ]
    assert rounds and rounds[0].input["round"] == 1


async def test_a_dropped_idea_proposed_again_stays_out_and_is_not_dropped_twice() -> None:
    world = FakeWorld()
    world.ideate_fn = ideator_repeating_idea_one()
    world.skeptic_fn = skeptic_raising(
        (1, 1, FATAL),
        (2, 2, {"severity": "minor", "dimension": "learning"}),  # keeps the debate going
    )
    h = DiscoverHarness(world)
    state = await run(h, count=3)
    assert state.card is not None
    assert [r["round"] for r in (b for b in world.bodies(*IDEATE))] == [1, 2]
    assert ref(1) not in {i["idea_id"] for i in state.card["ideas"]}
    assert ref(1) not in validated(world)
    assert len(h.events("idea_dropped")) == 1
    assert any("proposed again" in n for n in state.card["notes"])


async def test_an_objection_the_critic_rejected_drops_nothing() -> None:
    world = FakeWorld()
    world.skeptic_fn = skeptic_raising((7, 1, FATAL))
    world.filter_fn = lambda body: {
        "accepted": [],
        "rejected": [
            {"objection_id": o["objection_id"], "reason": "no evidence"} for o in body["objections"]
        ],
    }
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    assert state.card["dropped"] == []
    assert ref(1) in validated(world)
    assert h.events("idea_dropped") == []


async def test_without_the_critic_no_idea_is_dropped_on_unverified_objections() -> None:
    world = FakeWorld()
    world.skeptic_fn = skeptic_raising((7, 1, FATAL))
    world.faults[FILTER] = [503] * 10
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    assert state.card["dropped"] == []
    assert ref(1) in {i["idea_id"] for i in state.card["ideas"]}


@pytest.mark.parametrize(
    ("specs", "dropped"),
    [
        ([(1, 1, {"severity": "major", "dimension": "risk"})], False),  # one objection: kept
        (
            [
                (1, 1, {"severity": "minor", "dimension": "risk"}),
                (2, 1, {"severity": "minor", "dimension": "risk"}),
            ],
            False,
        ),  # same dimension twice: kept
        (
            [
                (1, 1, {"severity": "major", "dimension": "risk"}),
                (2, 1, {"severity": "major", "dimension": "learning"}),
            ],
            True,
        ),  # two dimensions: dropped
        ([(1, 1, FATAL)], True),
    ],
    ids=["one-major", "same-dimension-twice", "two-dimensions", "one-fatal"],
)
async def test_drop_rule_equivalence_classes(specs: list[Any], dropped: bool) -> None:
    world = FakeWorld()
    world.skeptic_fn = skeptic_raising(*specs)
    h = DiscoverHarness(world)
    state = await run(h)
    assert state.card is not None
    assert (ref(1) in {d["idea_id"] for d in state.card["dropped"]}) is dropped
    assert (ref(1) in {c["idea_ref"] for c in state.candidates}) is not dropped
    if dropped:
        assert ref(1) not in validated(world)
