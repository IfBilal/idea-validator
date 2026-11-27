"""Challenge run (U8): re-check one claim; defended, or removed with M11 recomputing a new version.

M11 runs first on the provisional version ``base + 1``; only then is the new version written to
M2, in one idempotent call that carries the card. So no failure leaves a version without a card.
"""

from typing import Any

import pytest

from svc_orchestrator.models.challenge_state import ChallengeState
from svc_orchestrator.models.state import RunStatus
from svc_orchestrator.testing import ChallengeHarness, FakeWorld, Harness, fake_verdict

VERDICT = ("verdict", "POST /verdict")
CHECK = ("critic", "POST /check")
PUT = "PUT /internal/ideas/idea_1/versions/2"


async def settled_card(world: FakeWorld | None = None) -> tuple[FakeWorld, dict[str, Any]]:
    """A real Validate run (v1) whose card is what M2 would hand back for the challenge."""
    world = world or FakeWorld()
    h = Harness(world)
    await h.start()
    card = (await h.state()).card
    assert card is not None and card["verdict"] is not None
    world.versions[("idea_1", 1)] = {
        "idea_id": "idea_1",
        "version": 1,
        "user_id": "u1",
        "text": "A CLI that lints Dockerfiles for slow layers",
        "card": card,
    }
    world.calls.clear()
    world.queries.clear()
    world.cache.clear()
    return world, card


async def challenge(h: ChallengeHarness, **over: Any) -> ChallengeState:
    return await h.start(**over)


def check_says(supported: bool, quote: str = "the quote", reason: str = "ok") -> Any:
    return lambda body: {
        "results": [
            {
                "claim_id": c["claim_id"],
                "supported": supported,
                "reason": reason,
                "supporting_quote": quote if supported else None,
            }
            for c in body["claims"]
        ]
    }


def order(world: FakeWorld) -> list[str]:
    return [route for _, route, _ in world.calls]


# ---- defended -------------------------------------------------------------------------------------
async def test_a_supported_claim_is_defended_with_its_quote_and_nothing_changes() -> None:
    world, card = await settled_card()
    world.check_fn = check_says(True, quote="hadolint lints Dockerfiles")
    h = ChallengeHarness(world)
    state = await challenge(h, claim_id="cl_risk")
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    original = next(c for c in card["claims"] if c["claim_id"] == "cl_risk")
    assert state.card == {
        "claim_id": "cl_risk",
        "outcome": "defended",
        "reason": "ok",
        "evidence_ids": original["evidence_ids"],
        "supporting_quote": "hadolint lints Dockerfiles",
    }
    assert world.count(*VERDICT) == 0
    assert world.creates == []
    assert h.event_types()[-1] == "run_completed"


async def test_the_claim_is_checked_with_its_own_text_evidence_and_dimension() -> None:
    world, card = await settled_card()
    world.check_fn = check_says(True)
    await challenge(ChallengeHarness(world), claim_id="cl_competition")
    (sent,) = world.bodies(*CHECK)[0]["claims"]
    original = next(c for c in card["claims"] if c["claim_id"] == "cl_competition")
    assert sent == {
        "claim_id": "cl_competition",
        "text": original["text"],
        "evidence_ids": original["evidence_ids"],
        "dimension": "competition",
    }


# ---- removed: M11 first, then one idempotent M2 write with the card ---------------------------------
async def test_an_unsupported_claim_is_removed_and_m11_recomputes_a_new_version() -> None:
    world, card = await settled_card()
    world.check_fn = check_says(False, reason="quote does not support")
    world.verdict_fn = lambda req: fake_verdict(req, "INSUFFICIENT_EVIDENCE", score=None)
    state = await challenge(ChallengeHarness(world), claim_id="cl_competition")
    assert state.status is RunStatus.SUCCEEDED
    result = state.card
    assert result is not None
    assert (result["claim_id"], result["outcome"], result["new_version"]) == (
        "cl_competition",
        "removed",
        2,
    )
    assert result["reason"] == "quote does not support"
    sent = world.bodies(*VERDICT)[0]
    old = card["verdict_input"]
    assert sent["version"] == 2
    assert sent["gate_inputs"] == old["gate_inputs"]
    assert sent["profile"] == old["profile"]
    assert sent["objections_accepted"] == old["objections_accepted"]
    by_dim = {s["dimension"]: s for s in sent["scores"]}
    assert by_dim["competition"]["score"] is None
    assert by_dim["competition"]["confidence"] == 0
    assert by_dim["competition"]["evidence_ids"] == []
    for s in old["scores"]:
        if s["dimension"] != "competition":
            assert by_dim[s["dimension"]]["score"] == s["score"]


async def test_m11_runs_before_the_version_is_written_and_the_write_carries_the_card() -> None:
    world, base = await settled_card()
    world.check_fn = check_says(False)
    world.verdict_fn = lambda req: fake_verdict(req, "PIVOT", score=55.5, confidence=0.4)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    calls = order(world)
    assert calls.index("POST /verdict") < calls.index(PUT)
    assert calls.count(PUT) == 1
    saved = world.versions[("idea_1", 2)]
    assert saved["text"] == world.versions[("idea_1", 1)]["text"]
    assert saved["change_note"] == "challenge removed cl_risk"
    assert saved["idempotency_key"] == "run_1"
    assert saved["card"]["verdict"] == "PIVOT"
    assert state.card is not None and state.card["card"] == saved["card"]
    assert world.creates == [("idea_1", 2)]
    assert world.versions[("idea_1", 1)]["card"] == base


async def test_the_new_card_is_what_m11_returned_and_records_the_removal() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    world.verdict_fn = lambda req: fake_verdict(req, "PIVOT", score=55.5, confidence=0.4)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.card is not None
    new = state.card["card"]
    assert (new["verdict"], new["score"], new["confidence"]) == ("PIVOT", 55.5, 0.4)
    assert new["version"] == 2 and new["idea_id"] == "idea_1"
    removed = {c["claim_id"]: c["removed"] for c in new["claims"]}
    assert removed["cl_risk"] is True
    assert [e["claim_id"] for e in new["challenge_log"]] == ["cl_risk"]
    assert new["partial"] is False
    assert new["verdict_input"] == world.bodies(*VERDICT)[0]


async def test_the_orchestrator_does_not_decide_the_verdict_after_a_challenge() -> None:
    for label in ("GO", "NO-GO"):
        world, _ = await settled_card()
        world.check_fn = check_says(False)
        world.label = label
        state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
        assert state.card is not None and state.card["card"]["verdict"] == label


async def test_a_claim_with_no_answer_from_the_critic_is_treated_as_unsupported() -> None:
    world, _ = await settled_card()
    world.check_fn = lambda body: {"results": []}
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.card is not None and state.card["outcome"] == "removed"


# ---- retries, double submits, concurrency, stale versions ---------------------------------------------
async def test_a_retried_write_still_creates_exactly_one_version() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    world.faults[("profile", PUT)] = [503, 503]
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.SUCCEEDED
    assert order(world).count(PUT) == 3  # two failures, then the one that landed
    assert world.creates == [("idea_1", 2)]


async def test_the_same_run_submitted_twice_creates_one_version_and_returns_the_same_result() -> (
    None
):
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    first = await challenge(ChallengeHarness(world, run_id="run_7"), claim_id="cl_risk")
    second = await challenge(ChallengeHarness(world, run_id="run_7"), claim_id="cl_risk")
    assert world.creates == [("idea_1", 2)]
    assert first.status is second.status is RunStatus.SUCCEEDED
    assert first.card is not None and second.card is not None
    assert second.card["new_version"] == first.card["new_version"] == 2


async def test_a_second_challenge_on_the_same_base_version_conflicts() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    first = await challenge(ChallengeHarness(world, run_id="run_a"), claim_id="cl_risk")
    second = await challenge(ChallengeHarness(world, run_id="run_b"), claim_id="cl_competition")
    assert first.status is RunStatus.SUCCEEDED
    assert second.status is RunStatus.FAILED
    assert "newer version" in (second.error or "")
    assert world.creates == [("idea_1", 2)]  # exactly one version, the first run's
    assert second.card is not None and second.card.get("card") is None


async def test_challenging_a_version_that_is_not_the_latest_conflicts() -> None:
    world, card = await settled_card()
    world.versions[("idea_1", 2)] = {
        "idea_id": "idea_1",
        "version": 2,
        "user_id": "u1",
        "text": "newer",
        "card": card,
        "idempotency_key": "someone-else",
    }
    world.check_fn = check_says(False)
    h = ChallengeHarness(world)
    state = await challenge(h, claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert "newer version" in (state.error or "")
    assert world.creates == []
    assert "verdict" not in h.event_types()


async def test_a_claim_that_was_already_removed_returns_the_existing_result() -> None:
    world, card = await settled_card()
    for claim in card["claims"]:
        claim["removed"] = claim["claim_id"] == "cl_risk"
    card["challenge_log"] = [{"claim_id": "cl_risk", "outcome": "removed", "reason": "no quote"}]
    h = ChallengeHarness(world)
    state = await challenge(h, claim_id="cl_risk")
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["outcome"] == "removed"
    assert state.card["already_removed"] is True
    assert state.card["reason"] == "no quote"
    assert state.card["new_version"] == 1
    assert state.card["card"] == card
    assert world.count(*CHECK) == 0 and world.count(*VERDICT) == 0
    assert world.creates == []


# ---- bad requests and failures ----------------------------------------------------------------------------
async def test_an_unknown_claim_fails_the_run_before_any_check() -> None:
    world, _ = await settled_card()
    state = await challenge(ChallengeHarness(world), claim_id="cl_nope")
    assert state.status is RunStatus.FAILED
    assert "claim" in (state.error or "")
    assert world.count(*CHECK) == 0


async def test_an_unknown_version_fails_the_run() -> None:
    world, _ = await settled_card()
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk", version=9)
    assert state.status is RunStatus.FAILED
    assert world.count(*CHECK) == 0


async def test_another_users_idea_is_not_found_and_the_owner_is_always_sent() -> None:
    world, _ = await settled_card()
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk", user_id="someone-else")
    assert state.status is RunStatus.FAILED
    assert world.count(*CHECK) == 0
    gets = [q for route, q in world.queries if route.startswith("GET /internal/ideas/")]
    assert gets == [{"user_id": "someone-else"}]


async def test_every_m2_version_call_carries_the_owner() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    await challenge(ChallengeHarness(world), claim_id="cl_risk")
    m2 = [q for route, q in world.queries if "/internal/ideas/" in route]
    assert len(m2) == 2 and all(q == {"user_id": "u1"} for q in m2)
    assert world.versions[("idea_1", 2)]["user_id"] == "u1"


async def test_a_partial_card_cannot_be_challenged() -> None:
    world, card = await settled_card()
    card["partial"] = True
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.count(*CHECK) == 0


async def test_a_claim_without_a_dimension_fails_closed() -> None:
    world, card = await settled_card()
    for claim in card["claims"]:
        claim.pop("dimension", None)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert "dimension" in (state.error or "")
    assert world.count(*CHECK) == 0


async def test_a_claim_whose_dimension_already_abstains_fails_closed() -> None:
    world, card = await settled_card()
    for score in card["verdict_input"]["scores"]:
        if score["dimension"] == "risk":
            score.update(score=None, confidence=0, evidence_ids=[])
    world.check_fn = check_says(False)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert "abstain" in (state.error or "")
    assert world.count(*VERDICT) == 0 and world.creates == []


async def test_a_card_without_its_recorded_m11_inputs_cannot_be_recomputed() -> None:
    world, card = await settled_card()
    del card["verdict_input"]
    world.check_fn = check_says(False)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert "recorded" in (state.error or "")
    assert world.creates == []
    assert world.count(*VERDICT) == 0


async def test_when_the_critic_is_down_nothing_is_removed() -> None:
    world, _ = await settled_card()
    world.faults[CHECK] = [503, 503, 503]
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.creates == []
    assert world.count(*VERDICT) == 0


async def test_when_m2_cannot_save_the_version_no_card_is_shown_as_saved() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    world.faults[("profile", PUT)] = [422]
    h = ChallengeHarness(world)
    state = await challenge(h, claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.count(*VERDICT) == 1  # M11 ran first, as designed
    assert state.card is not None and state.card.get("card") is None
    assert "new_version" not in state.card
    assert "verdict" not in h.event_types()


async def test_when_m11_is_down_no_version_is_written_and_no_verdict_is_invented() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    world.faults[VERDICT] = [503, 503, 503]
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.creates == []
    assert state.card is not None
    assert state.card["outcome"] == "removed"
    assert state.card.get("card") is None


@pytest.mark.parametrize("override", [{"idea_id": "idea_other"}, {"version": 99}])
async def test_a_verdict_for_another_idea_or_version_fails_the_run(
    override: dict[str, Any],
) -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    world.verdict_fn = lambda req: fake_verdict(req, "GO", **override)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.creates == []
    assert state.card is not None and state.card.get("card") is None


async def test_an_exhausted_budget_stops_the_run() -> None:
    world, _ = await settled_card()
    h = ChallengeHarness(world, limits={"max_steps": 1})
    state = await challenge(h, claim_id="cl_risk")
    assert state.status is RunStatus.BUDGET_STOPPED
    assert state.card is not None and state.card["partial"] is True
    assert h.event_types()[-1] == "budget_stopped"


async def test_a_failed_challenge_still_carries_the_claim_id() -> None:
    world, _ = await settled_card()
    state = await challenge(ChallengeHarness(world), claim_id="cl_nope")
    assert state.card is not None
    assert state.card["claim_id"] == "cl_nope"
    assert state.card["partial"] is True


async def test_every_call_is_traced_without_the_claim_text() -> None:
    world, card = await settled_card()
    world.check_fn = check_says(True)
    h = ChallengeHarness(world)
    await challenge(h, claim_id="cl_risk")
    assert {r.tool for r in h.records} >= {"m2.get_idea_version", "m12.check"}
    text = next(c for c in card["claims"] if c["claim_id"] == "cl_risk")["text"]
    assert text not in "".join(str(r.input) for r in h.records)
