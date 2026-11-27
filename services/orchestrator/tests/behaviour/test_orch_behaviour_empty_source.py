"""Behaviour: the knowledge base has no evidence at all (docs/harness/14 section 3, gate 9).

Evidence or abstain: every scorer abstains, M11 (the fake here) answers INSUFFICIENT_EVIDENCE,
nothing is invented (no claim, no evidence id, no competitor) and the run still succeeds.
"""

import json

import pytest

from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import FakeWorld, Harness, fake_verdict

SCORE_ALL = ("validators", "POST /score-all")
VERDICT = ("verdict", "POST /verdict")
CHECK = ("critic", "POST /check")
SCOUT = ("scouts", "POST /scout")
EFFORT = {"estimated_effort_hours": 50}


def empty_world() -> FakeWorld:
    world = FakeWorld()
    world.evidence = []
    world.verdict_fn = lambda req: fake_verdict(
        req, "INSUFFICIENT_EVIDENCE", score=None, confidence=0.0
    )
    return world


async def run(h: Harness) -> ValidateState:
    await h.start(overrides=EFFORT)
    return await h.state()


async def test_an_empty_source_ends_in_insufficient_evidence_with_every_scorer_abstaining() -> None:
    world = empty_world()
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert state.card["partial"] is False
    sent = world.bodies(*VERDICT)[0]
    assert len(sent["scores"]) == 6
    assert all(s["score"] is None and s["confidence"] == 0 for s in sent["scores"])
    assert all(s["evidence_ids"] == [] for s in sent["scores"])
    assert sent["gate_inputs"]["n_sources"] == 0
    assert sent["gate_inputs"]["n_platforms"] == 0
    assert h.event_types()[-2:] == ["verdict", "run_completed"]


async def test_nothing_is_invented_no_claim_no_evidence_no_competitor() -> None:
    world = empty_world()
    state = await run(Harness(world))
    assert state.card is not None
    assert state.card["claims"] == []
    assert not state.card["evidence"]
    assert world.count(*CHECK) == 0  # no claim exists, so nothing to check
    text = json.dumps(state.card)
    assert "card_" not in text  # no evidence id of any kind
    assert "Product" not in text  # no competitor name (the KB would have named them)


async def test_the_scorers_are_called_with_an_empty_evidence_list_not_made_up_context() -> None:
    world = empty_world()
    await run(Harness(world))
    assert world.bodies(*SCORE_ALL)[0]["evidence"] == []


async def test_a_scorer_that_invents_evidence_for_an_empty_source_is_forced_to_abstain() -> None:
    world = empty_world()
    world.score_fn = lambda body: {
        "scores": [
            {
                "dimension": d,
                "score": 4.5,
                "confidence": 0.9,
                "evidence_ids": ["card_deadbeef#c0"],  # not in the KB
                "reason": "Competitor Acme already does this",
                "rubric_version": f"{d}@v1",
                "model": "m",
            }
            for d in (
                "demand_pain",
                "competition",
                "differentiation",
                "feasibility",
                "risk",
                "learning",
            )
        ],
        "extra": {"estimated_effort_hours": 50},
    }
    state = await run(Harness(world))
    sent = world.bodies(*VERDICT)[0]["scores"]
    assert all(s["score"] is None and s["confidence"] == 0 for s in sent)
    assert "Acme" not in json.dumps(state.card)
    assert state.status is RunStatus.SUCCEEDED


async def test_an_empty_source_scouts_once_then_gives_up_without_looping() -> None:
    world = empty_world()
    state = await run(Harness(world))
    assert world.count(*SCOUT) == 3  # one pass over the three sources
    assert world.count("kb", "POST /search") == 8  # before and after the scouting, never more
    assert state.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize("pages", [0, 20])
async def test_with_or_without_deep_search_the_outcome_is_the_same(pages: int) -> None:
    world = empty_world()
    state = await run(Harness(world, deep_search_pages=pages))
    assert world.count(*SCOUT) == (0 if pages == 0 else 3)
    assert state.card is not None
    assert state.card["verdict"] == "INSUFFICIENT_EVIDENCE"


async def test_scouting_that_finds_nothing_does_not_degrade_or_invent() -> None:
    world = empty_world()
    world.scout_results = {"fetched": 0, "raw_page_ids": []}
    world.after_scout = []
    state = await run(Harness(world))
    assert state.n_sources == 0
    assert state.card is not None and not state.card["evidence"]


async def test_the_engine_decides_even_for_an_empty_source() -> None:
    # M3 does not special-case emptiness into a verdict of its own: whatever M11 says is forwarded
    world = empty_world()
    world.verdict_fn = None
    world.label = "NO-GO"
    state = await run(Harness(world))
    assert state.card is not None and state.card["verdict"] == "NO-GO"
