"""Behaviour: a scorer answers with invalid JSON, or fails and recovers (docs/harness/14 section 3).

M9 and M10 retry and fall back inside their own module (docs/harness/12). What M3 owes is
this: a bad answer never becomes a score, the dimension abstains (score null, confidence 0),
the verdict still comes only from M11 and the run still ends with a card.
"""

from typing import Any

import pytest

from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import FakeWorld, Harness, fake_verdict

SCORE_ALL = ("validators", "POST /score-all")
VERDICT = ("verdict", "POST /verdict")
EFFORT = {"estimated_effort_hours": 50}  # the user supplies it, so no question interrupts the run


async def run(h: Harness) -> ValidateState:
    await h.start(overrides=EFFORT)
    return await h.state()


def sent_scores(world: FakeWorld) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = world.bodies(*VERDICT)[0]["scores"]
    return scores


def assert_ended_with_m11_card(state: ValidateState, label: str) -> None:
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["verdict"] == label  # M11's word, whatever the scorer did


async def test_garbage_200_body_makes_every_dimension_abstain_and_m11_still_decides() -> None:
    world = FakeWorld()
    world.faults[SCORE_ALL] = ["garbage"]
    world.verdict_fn = lambda req: fake_verdict(
        req, "INSUFFICIENT_EVIDENCE", score=None, confidence=0.0
    )
    h = Harness(world)
    state = await run(h)
    assert world.count(*SCORE_ALL) == 1  # a bad body is a module bug: not retried, not repaired
    scores = sent_scores(world)
    assert len(scores) == 6
    assert all(s["score"] is None and s["confidence"] == 0 for s in scores)
    assert all(s["evidence_ids"] == [] for s in scores)
    assert state.degraded is True
    assert state.card is not None and state.card["degraded"] is True
    assert_ended_with_m11_card(state, "INSUFFICIENT_EVIDENCE")
    assert world.cache == {}  # a degraded card is never cached
    assert "run_completed" in h.event_types()


async def test_the_card_verdict_is_what_m11_returned_not_what_the_scores_suggest() -> None:
    world = FakeWorld()
    world.faults[SCORE_ALL] = ["garbage"]
    world.label = "GO"  # the fake engine says GO even though every dimension abstained
    state = await run(Harness(world))
    assert all(s["score"] is None for s in sent_scores(world))
    assert_ended_with_m11_card(state, "GO")


async def test_one_malformed_dimension_abstains_and_the_valid_ones_survive() -> None:
    world = FakeWorld()

    def scorer(body: dict[str, Any]) -> Any:
        scores = world._scores(body)
        scores[1] = {"dimension": "competition", "score": "score: high"}  # not a number
        return {"scores": scores, "extra": world.extra}

    world.score_fn = scorer
    state = await run(Harness(world))
    by_dim = {s["dimension"]: s for s in sent_scores(world)}
    assert by_dim["competition"]["score"] is None
    assert by_dim["competition"]["confidence"] == 0
    assert by_dim["demand_pain"]["score"] == 3.5
    real = {e["evidence_id"] for e in world.evidence}
    assert by_dim["demand_pain"]["evidence_ids"]
    assert set(by_dim["demand_pain"]["evidence_ids"]) <= real
    assert_ended_with_m11_card(state, "GO")


@pytest.mark.parametrize("fault", ["timeout", 503, 429])
async def test_a_retry_that_succeeds_gives_real_scores_and_a_clean_card(fault: int | str) -> None:
    world = FakeWorld()
    world.faults[SCORE_ALL] = [fault]
    h = Harness(world)
    state = await run(h)
    assert world.count(*SCORE_ALL) == 2
    assert all(s["score"] == 3.5 for s in sent_scores(world))
    assert state.degraded is False
    assert_ended_with_m11_card(state, "GO")
    record = next(r for r in h.records if r.tool == "m9.score_all")
    assert (record.retries, record.error) == (1, None)


async def test_a_persistent_failure_abstains_after_the_bounded_retries() -> None:
    world = FakeWorld()
    world.faults[SCORE_ALL] = ["timeout"] * 10
    h = Harness(world)
    state = await run(h)
    assert world.count(*SCORE_ALL) == 3  # one try and two retries, never more
    assert all(s["score"] is None and s["confidence"] == 0 for s in sent_scores(world))
    assert state.degraded is True
    assert_ended_with_m11_card(state, "GO")
    record = next(r for r in h.records if r.tool == "m9.score_all")
    assert record.error is not None


@pytest.mark.parametrize(
    ("max_retries", "failures", "calls", "abstains"),
    [
        (0, 1, 1, True),  # no retry allowed: one failure is final
        (1, 1, 2, False),  # one retry recovers one failure
        (1, 2, 2, True),  # ... but not two
        (2, 2, 3, False),  # the default: two retries recover two failures
        (2, 3, 3, True),  # ... and a third failure abstains, with no fourth attempt
    ],
)
async def test_retry_boundaries(
    max_retries: int, failures: int, calls: int, abstains: bool
) -> None:
    world = FakeWorld()
    world.faults[SCORE_ALL] = ["timeout"] * failures
    state = await run(Harness(world, max_retries=max_retries))
    assert world.count(*SCORE_ALL) == calls
    assert all((s["score"] is None) is abstains for s in sent_scores(world))
    assert state.card is not None
    assert state.card["degraded"] is abstains
