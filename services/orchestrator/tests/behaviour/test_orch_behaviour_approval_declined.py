"""Behaviour: the run needs approval for a paid step and the user declines (docs/harness/07).

Approval is requested for deep search. Declining skips that step: no scouting, no raised cost
limit, no paid page; the run goes on with the evidence it already has, finishes without a
verdict made up by M3, and says that the search was declined. An unanswered approval counts as a
decline.
"""

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from svc_orchestrator.models.api import StartRun
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.services.runs import RunService
from svc_orchestrator.testing import FakeWorld, Harness, Rig

SCOUT = ("scouts", "POST /scout")
VERDICT = ("verdict", "POST /verdict")
EFFORT = {"estimated_effort_hours": 50}


def world_over_budget() -> FakeWorld:
    world = FakeWorld()
    world.evidence = world.evidence[:1]  # thin evidence asks for a deep search ...
    world.needs_approval = True  # ... which M12 says is over the soft budget
    return world


async def declined(world: FakeWorld, decision: Any = "decline") -> tuple[Harness, ValidateState]:
    h = Harness(world)
    first = await h.start(overrides=EFFORT)
    assert "__interrupt__" in first
    await h.resume(decision)
    return h, await h.state()


async def test_a_run_over_the_soft_budget_waits_for_the_user_and_pays_nothing_meanwhile() -> None:
    world = world_over_budget()
    h = Harness(world)
    await h.start(overrides=EFFORT)
    pending = await h.pending()
    assert pending is not None and pending["type"] == "approval"
    assert world.count(*SCOUT) == 0
    assert world.count("validators", "POST /score-all") == 0
    assert (await h.ledger.spent()).pages == 0
    assert "approval_required" in h.event_types()


async def test_declining_makes_no_further_paid_calls_and_leaves_the_limits_alone() -> None:
    world = world_over_budget()
    h, state = await declined(world)
    assert world.count(*SCOUT) == 0
    assert (await h.ledger.spent()).pages == 0
    limits = await h.ledger.limits()
    assert limits.max_cost_usd == pytest.approx(0.05)  # not raised
    assert state.approval_decision == "declined"
    assert state.pending_approval is None


async def test_declining_ends_cleanly_with_a_card_whose_verdict_is_m11s_and_a_note() -> None:
    world = world_over_budget()
    h, state = await declined(world)
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["verdict"] == "GO"  # the M11 fake's answer, not decided by M3 or the user
    assert world.count(*VERDICT) == 1
    assert any("declined" in n for n in state.card["notes"])
    types = h.event_types()
    assert types.count("approval_required") == 1
    assert types[-1] == "run_completed"
    assert "run_failed" not in types and "budget_stopped" not in types


async def test_the_decline_is_traced_as_a_decision() -> None:
    h, _ = await declined(world_over_budget())
    outcomes = [d for d in h.decisions() if d.output.get("decision") == "approval_outcome"]
    assert len(outcomes) == 1


@pytest.mark.parametrize("answer", ["decline", "no", "", "maybe", 0])
async def test_anything_but_an_explicit_approve_is_a_decline(answer: Any) -> None:
    world = world_over_budget()
    _, state = await declined(world, answer)
    assert world.count(*SCOUT) == 0
    assert state.approval_decision == "declined"


async def test_approving_is_the_only_path_that_scouts() -> None:
    world = world_over_budget()
    h = Harness(world)
    await h.start(overrides=EFFORT)
    await h.resume("approve")
    assert world.count(*SCOUT) == 3
    assert (await h.state()).approval_decision == "approved"


# ---- through the run service: what the user's client sees ------------------------------------
def start_body() -> StartRun:
    return StartRun.model_validate(
        {
            "kind": "validate",
            "user_id": "u1",
            "input": {
                "idea_text": "A CLI that lints Dockerfiles",
                "idea_id": "idea_1",
                "version": 1,
            },
        }
    )


def service_for(world: FakeWorld, **rig: Any) -> tuple[RunService, Rig]:
    rig_ = Rig(world, **rig)
    service = RunService(
        context_factory=rig_.context,
        checkpointer=MemorySaver(),
        config=rig_.config,
        question_timeout_s=600,
    )
    return service, rig_


async def test_through_the_api_a_declined_run_succeeds_and_reports_the_decline() -> None:
    world = world_over_budget()
    service, _ = service_for(world)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    view = await service.get(run_id)
    assert view.state == "awaiting_approval"
    assert view.pending_approval is not None
    await service.approve(run_id, view.pending_approval["approval_id"], "decline")
    await service.wait(run_id)
    view = await service.get(run_id)
    assert view.state == "succeeded"
    assert world.count(*SCOUT) == 0
    assert view.result is not None
    assert any("declined" in n for n in view.result["notes"])
    await service.aclose()


async def test_through_the_api_an_unanswered_approval_expires_as_declined() -> None:
    world = world_over_budget()
    service, _ = service_for(world, approval_timeout_s=0.05)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    view = await service.get(run_id)
    for _ in range(100):
        view = await service.get(run_id)
        if view.state == "succeeded":
            break
        await asyncio.sleep(0.02)
    assert view.state == "succeeded"
    assert world.count(*SCOUT) == 0
    await service.aclose()
