"""RunService: background runs, interrupts, answer/approve, timeouts, persistence."""

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from common.errors import Conflict, NotFound
from svc_orchestrator.models.api import StartRun
from svc_orchestrator.models.state import RunStatus
from svc_orchestrator.services.runs import RunService
from svc_orchestrator.testing import FakeWorld, Rig, fake_verdict, voi_response


def start_body(**input_over: Any) -> StartRun:
    return StartRun.model_validate(
        {
            "kind": "validate",
            "user_id": "u1",
            "input": {
                "idea_text": "A CLI that lints Dockerfiles",
                "idea_id": "idea_1",
                "version": 1,
                **input_over,
            },
        }
    )


def make(
    world: FakeWorld | None = None, *, question_timeout_s: float = 600, **rig_options: Any
) -> tuple[RunService, Rig]:
    rig = Rig(world, **rig_options)
    service = RunService(
        context_factory=rig.context,
        checkpointer=MemorySaver(),
        config=rig.config,
        question_timeout_s=question_timeout_s,
    )
    return service, rig


def asking_world() -> FakeWorld:
    world = FakeWorld()
    world.voi_fn = lambda body: voi_response(True)
    world.verdict_fn = lambda req: fake_verdict(
        req, "NO-GO" if req["gate_inputs"]["estimated_effort_hours"] > 150 else "GO"
    )
    return world


def thin_world(needs_approval: bool = True) -> FakeWorld:
    world = FakeWorld()
    world.evidence = world.evidence[:1]
    world.needs_approval = needs_approval
    return world


async def finished(service: RunService, run_id: str) -> Any:
    await service.wait(run_id)
    return await service.get(run_id)


# ---- start / get -----------------------------------------------------------------------------
async def test_start_returns_a_run_id_and_the_run_completes_in_the_background() -> None:
    service, _ = make()
    run_id = await service.start(start_body())
    assert run_id.startswith("run_")
    view = await finished(service, run_id)
    assert view.state == "succeeded"
    assert view.kind == "validate"
    assert view.result is not None
    assert view.result["verdict"] == "GO"
    assert view.spent["steps"] > 0
    assert view.budget["max_steps"] == 80
    assert view.pending_question is None and view.pending_approval is None
    assert view.progress["step"] == "CARD"
    await service.aclose()


async def test_start_does_not_wait_for_the_run() -> None:
    world = FakeWorld()
    service, _ = make(world)
    run_id = await service.start(start_body())
    assert (await service.get(run_id)).state in {"created", "running", "succeeded"}
    await service.wait(run_id)
    await service.aclose()


async def test_each_run_has_its_own_id_budget_and_events() -> None:
    service, rig = make()
    a = await service.start(start_body())
    b = await service.start(start_body(idea_id="idea_2"))
    assert a != b
    for run_id in (a, b):
        assert (await finished(service, run_id)).state == "succeeded"
    assert {"budget:" + a, "budget:" + b} <= set(rig.redis.hashes)
    assert rig.publisher.events_of(a) and rig.publisher.events_of(b)
    assert all(e.run_id == a for e in rig.publisher.events_of(a))
    await service.aclose()


async def test_run_events_start_with_run_started_and_end_with_run_completed() -> None:
    service, rig = make()
    run_id = await service.start(start_body())
    await service.wait(run_id)
    types = [e.type for e in rig.publisher.events_of(run_id)]
    assert types[0] == "run_started"
    assert types[-1] == "run_completed"
    first = rig.publisher.events_of(run_id)[0]
    assert first.payload["kind"] == "validate"
    assert first.payload["budget"]["max_steps"] == 80
    await service.aclose()


async def test_the_run_id_is_set_on_every_module_call() -> None:
    service, rig = make()
    run_id = await service.start(start_body())
    await service.wait(run_id)
    assert {r.run_id for r in rig.records} == {run_id}
    await service.aclose()


async def test_an_unknown_run_is_not_found() -> None:
    service, _ = make()
    with pytest.raises(NotFound):
        await service.get("run_nope")
    with pytest.raises(NotFound):
        await service.answer("run_nope", "q", 1)
    with pytest.raises(NotFound):
        await service.approve("run_nope", "a", "approve")


async def test_the_run_state_is_persisted_to_m2() -> None:
    service, rig = make()
    run_id = await service.start(start_body())
    await service.wait(run_id)
    record = rig.world.runs[run_id]
    assert record["state"] == "succeeded"
    assert record["kind"] == "validate"
    assert record["user_id"] == "u1"
    assert record["result"]["verdict"] == "GO"
    assert rig.world.count("profile", f"PUT /internal/runs/{run_id}") >= 2  # running + finished
    await service.aclose()


async def test_a_failing_run_store_does_not_fail_the_run() -> None:
    world = FakeWorld()
    world.faults[("profile", "PUT /internal/runs/x")] = []
    original = world._answer

    def failing(route: str, path: str, body: Any) -> tuple[int, Any]:
        if route.startswith("PUT /internal/runs/"):
            return 400, {}
        return original(route, path, body)

    world._answer = failing  # type: ignore[method-assign]
    service, _ = make(world)
    run_id = await service.start(start_body())
    assert (await finished(service, run_id)).state == "succeeded"
    await service.aclose()


# ---- questions -------------------------------------------------------------------------------
async def test_a_run_waiting_for_an_answer_exposes_the_question() -> None:
    service, rig = make(asking_world())
    run_id = await service.start(start_body())
    view = await finished(service, run_id)
    assert view.state == "awaiting_input"
    assert view.pending_question is not None
    assert view.pending_question["question_id"] == "q_estimated_effort_hours"
    assert view.result is None
    assert rig.world.runs[run_id]["state"] == "awaiting_input"
    await service.aclose()


async def test_answering_resumes_and_the_answer_changes_the_verdict_via_m11() -> None:
    service, _ = make(asking_world())
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.answer(run_id, "q_estimated_effort_hours", 300)
    view = await finished(service, run_id)
    assert view.state == "succeeded"
    assert view.result is not None
    assert view.result["verdict"] == "NO-GO"
    assert view.pending_question is None
    await service.aclose()


async def test_a_low_answer_gives_go() -> None:
    service, _ = make(asking_world())
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.answer(run_id, "q_estimated_effort_hours", 60)
    assert (await finished(service, run_id)).result["verdict"] == "GO"
    await service.aclose()


async def test_a_wrong_question_id_is_a_conflict_and_the_run_keeps_waiting() -> None:
    service, _ = make(asking_world())
    run_id = await service.start(start_body())
    await service.wait(run_id)
    with pytest.raises(Conflict):
        await service.answer(run_id, "q_other", 10)
    assert (await service.get(run_id)).state == "awaiting_input"
    await service.aclose()


async def test_answering_a_run_that_is_not_waiting_is_a_conflict() -> None:
    service, _ = make()
    run_id = await service.start(start_body())
    await service.wait(run_id)
    with pytest.raises(Conflict):
        await service.answer(run_id, "q_estimated_effort_hours", 10)
    await service.aclose()


async def test_a_second_answer_to_the_same_question_is_a_conflict() -> None:
    service, _ = make(asking_world())
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.answer(run_id, "q_estimated_effort_hours", 60)
    with pytest.raises(Conflict):
        await service.answer(run_id, "q_estimated_effort_hours", 300)
    await service.wait(run_id)
    await service.aclose()


async def test_an_unanswered_question_times_out_and_the_run_continues_with_a_default() -> None:
    service, _ = make(asking_world(), question_timeout_s=0.05)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    for _ in range(100):  # the timeout task resumes the run in the background
        view = await service.get(run_id)
        if view.state == "succeeded":
            break
        await asyncio.sleep(0.02)
    assert view.state == "succeeded"
    assert view.result is not None
    assert view.result["verdict"] == "GO"  # the scorer's own estimate (100 h)
    assert any("no answer" in n for n in view.result["notes"])
    await service.aclose()


async def test_an_answer_before_the_timeout_cancels_it() -> None:
    service, _ = make(asking_world(), question_timeout_s=0.3)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.answer(run_id, "q_estimated_effort_hours", 300)
    view = await finished(service, run_id)
    await asyncio.sleep(0.4)
    assert (await service.get(run_id)).result == view.result  # not overwritten by a late timeout
    assert view.result is not None and view.result["verdict"] == "NO-GO"
    await service.aclose()


# ---- approvals -------------------------------------------------------------------------------
async def test_a_run_needing_approval_exposes_the_request() -> None:
    service, _ = make(thin_world())
    run_id = await service.start(start_body())
    view = await finished(service, run_id)
    assert view.state == "awaiting_approval"
    assert view.pending_approval is not None
    assert view.pending_approval["approval_id"] == f"ap_{run_id}_deep_search"
    await service.aclose()


async def test_approving_continues_with_deep_search() -> None:
    world = thin_world()
    service, _ = make(world)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.approve(run_id, f"ap_{run_id}_deep_search", "approve")
    view = await finished(service, run_id)
    assert view.state == "succeeded"
    assert world.count("scouts", "POST /scout") == 3
    await service.aclose()


async def test_declining_continues_without_deep_search() -> None:
    world = thin_world()
    service, _ = make(world)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.approve(run_id, f"ap_{run_id}_deep_search", "decline")
    view = await finished(service, run_id)
    assert view.state == "succeeded"
    assert world.count("scouts", "POST /scout") == 0
    assert view.result is not None
    assert any("declined" in n for n in view.result["notes"])
    await service.aclose()


async def test_a_wrong_approval_id_is_a_conflict() -> None:
    service, _ = make(thin_world())
    run_id = await service.start(start_body())
    await service.wait(run_id)
    with pytest.raises(Conflict):
        await service.approve(run_id, "ap_other", "approve")
    await service.aclose()


async def test_approving_a_run_that_is_not_waiting_is_a_conflict() -> None:
    service, _ = make()
    run_id = await service.start(start_body())
    await service.wait(run_id)
    with pytest.raises(Conflict):
        await service.approve(run_id, "x", "approve")
    await service.aclose()


async def test_approving_while_a_question_is_pending_is_a_conflict() -> None:
    service, _ = make(asking_world())
    run_id = await service.start(start_body())
    await service.wait(run_id)
    with pytest.raises(Conflict):
        await service.approve(run_id, "x", "approve")
    await service.aclose()


async def test_an_unanswered_approval_expires_as_declined() -> None:
    world = thin_world()
    service, _ = make(world, approval_timeout_s=0.05)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    for _ in range(100):
        view = await service.get(run_id)
        if view.state == "succeeded":
            break
        await asyncio.sleep(0.02)
    assert view.state == "succeeded"
    assert world.count("scouts", "POST /scout") == 0
    await service.aclose()


# ---- failures and budgets ------------------------------------------------------------------------
async def test_an_unexpected_error_fails_the_run_without_leaking_details() -> None:
    service, rig = make()

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret internal detail /etc/passwd")

    rig.clients.kb.search = boom  # type: ignore[method-assign]
    run_id = await service.start(start_body())
    view = await finished(service, run_id)
    assert view.state == "failed"
    assert view.error == "internal error"
    assert "secret" not in str(view)
    assert rig.publisher.events_of(run_id)[-1].type == "run_failed"
    assert rig.world.runs[run_id]["state"] == "failed"
    await service.aclose()


async def test_a_module_failure_is_reported_as_a_failed_run_with_a_partial_result() -> None:
    world = FakeWorld()
    world.faults[("verdict", "POST /verdict")] = [503] * 3
    service, _ = make(world)
    run_id = await service.start(start_body())
    view = await finished(service, run_id)
    assert view.state == "failed"
    assert view.error is not None and "VERDICT" in view.error
    assert view.result is not None and view.result["partial"] is True
    await service.aclose()


async def test_a_budget_stopped_run_reports_the_partial_result() -> None:
    service, rig = make(limits={"max_steps": 8})
    run_id = await service.start(start_body())
    view = await finished(service, run_id)
    assert view.state == "budget_stopped"
    assert view.result is not None and view.result["partial"] is True
    assert rig.publisher.events_of(run_id)[-1].type == "budget_stopped"
    await service.aclose()


async def test_aclose_cancels_pending_timeouts_and_tasks() -> None:
    service, _ = make(asking_world(), question_timeout_s=30)
    run_id = await service.start(start_body())
    await service.wait(run_id)
    await service.aclose()  # must not hang or raise
    assert RunStatus.AWAITING_INPUT.value == (await service.get(run_id)).state


async def test_progress_reports_the_step_while_waiting() -> None:
    service, _ = make(asking_world())
    run_id = await service.start(start_body())
    view = await finished(service, run_id)
    assert view.progress["step"] == "WAIT_ANSWER"
    await service.aclose()
