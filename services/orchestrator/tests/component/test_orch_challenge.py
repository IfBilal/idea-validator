"""Challenge run (U8): re-check one claim; defended, or removed with M11 recomputing a new version."""

from typing import Any

import pytest

from svc_orchestrator.models.challenge_state import ChallengeState
from svc_orchestrator.models.state import RunStatus
from svc_orchestrator.testing import ChallengeHarness, FakeWorld, Harness, fake_verdict

VERDICT = ("verdict", "POST /verdict")
CHECK = ("critic", "POST /check")


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
        "text": "A CLI that lints Dockerfiles for slow layers",
        "card": card,
    }
    world.calls.clear()
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


# ---- defended -------------------------------------------------------------------------------------
async def test_a_supported_claim_is_defended_with_its_quote_and_nothing_changes() -> None:
    world, card = await settled_card()
    world.check_fn = check_says(True, quote="hadolint lints Dockerfiles")
    h = ChallengeHarness(world)
    state = await challenge(h, claim_id="cl_risk")
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card == {
        "claim_id": "cl_risk",
        "outcome": "defended",
        "reason": "ok",
        "evidence_ids": next(c for c in card["claims"] if c["claim_id"] == "cl_risk")[
            "evidence_ids"
        ],
        "supporting_quote": "hadolint lints Dockerfiles",
    }
    assert world.count(*VERDICT) == 0
    assert ("idea_1", 2) not in world.versions
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


# ---- removed ------------------------------------------------------------------------------------------
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
    assert world.versions[("idea_1", 2)]["change_note"] == "challenge removed cl_competition"
    assert world.versions[("idea_1", 2)]["text"] == world.versions[("idea_1", 1)]["text"]
    # M11 got the recorded inputs, with exactly that dimension abstained
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


async def test_a_new_version_number_comes_from_m2_not_from_a_guess() -> None:
    world, _ = await settled_card()
    world.versions[("idea_1", 2)] = {"idea_id": "idea_1", "version": 2, "text": "x", "card": {}}
    world.check_fn = check_says(False)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.card is not None and state.card["new_version"] == 3
    assert world.bodies(*VERDICT)[0]["version"] == 3


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


async def test_a_claim_that_was_already_removed_cannot_be_challenged_again() -> None:
    world, card = await settled_card()
    for claim in card["claims"]:
        claim["removed"] = claim["claim_id"] == "cl_risk"
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert "already removed" in (state.error or "")
    assert world.count(*CHECK) == 0


async def test_a_partial_card_cannot_be_challenged() -> None:
    world, card = await settled_card()
    card["partial"] = True
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.count(*CHECK) == 0


async def test_a_card_without_its_recorded_m11_inputs_cannot_be_recomputed() -> None:
    world, card = await settled_card()
    del card["verdict_input"]
    world.check_fn = check_says(False)
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert "recorded" in (state.error or "")
    assert ("idea_1", 2) not in world.versions
    assert world.count(*VERDICT) == 0


async def test_when_the_critic_is_down_nothing_is_removed() -> None:
    world, _ = await settled_card()
    world.faults[CHECK] = [503, 503, 503]
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert ("idea_1", 2) not in world.versions
    assert world.count(*VERDICT) == 0


async def test_when_m2_cannot_create_the_version_m11_is_not_called() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    original = world._discover_answer

    def broken(route: str, path: str, body: Any) -> tuple[int, Any]:
        if route.startswith("POST /internal/ideas/"):
            return 422, {}
        return original(route, path, body)

    world._discover_answer = broken  # type: ignore[method-assign]
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert world.count(*VERDICT) == 0


async def test_when_m11_is_down_the_run_fails_and_no_verdict_is_invented() -> None:
    world, _ = await settled_card()
    world.check_fn = check_says(False)
    world.faults[VERDICT] = [503, 503, 503]
    state = await challenge(ChallengeHarness(world), claim_id="cl_risk")
    assert state.status is RunStatus.FAILED
    assert state.card is not None
    assert state.card["outcome"] == "removed"
    assert "new_version" not in state.card or state.card.get("card") is None
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
