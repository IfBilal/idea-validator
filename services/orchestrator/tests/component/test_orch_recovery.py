"""Runs survive an M3 restart (docs/harness/07 §1): rehydrate from the checkpoint and M2's mirror.

A "replica" is a ``RunService`` with its own owner id. Replicas of one test share the fake world
(M2 and friends), the Redis (budget ledger, events) and the checkpoint saver, which is exactly what
separates a restart from a new run in production. Crashing a replica cancels its tasks without
releasing its leases.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from common.budget import Spend
from common.errors import Conflict, NotFound, UpstreamError
from svc_orchestrator.models.api import StartRun
from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.repositories.attempts import InMemoryAttempts
from svc_orchestrator.repositories.leases import InMemoryLeases
from svc_orchestrator.repositories.serde import checkpoint_serde
from svc_orchestrator.services.recovery import recovery_loop
from svc_orchestrator.services.runs import RECOVERY_FAILED, RunService
from svc_orchestrator.testing import FakeWorld, Rig, fake_verdict, voi_response


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Cluster:
    """What survives a restart, plus a factory for replicas."""

    def __init__(self, world: FakeWorld | None = None, **rig_options: Any) -> None:
        self.world = world or FakeWorld()
        self.saver = MemorySaver(serde=checkpoint_serde())
        self.leases: dict[str, tuple[str, float]] = {}
        self.attempts: dict[str, int] = {}
        self.clock = Clock()
        self.rig_options = rig_options
        self.first: Rig | None = None
        self.replicas: list[RunService] = []

    def replica(
        self, owner: str, *, fresh_redis: bool = False, **service_options: Any
    ) -> RunService:
        rig = Rig(self.world, **self.rig_options)
        if self.first is None:
            self.first = rig
        elif not fresh_redis:
            rig.redis, rig.publisher = self.first.redis, self.first.publisher
        options: dict[str, Any] = {"question_timeout_s": 600}
        options.update(service_options)
        service = RunService(
            context_factory=lambda run_id, kind: rig.context(run_id, kind, owner=owner),
            checkpointer=self.saver,
            config=rig.config,
            leases=InMemoryLeases(self.leases, owner, ttl_s=60, clock=self.clock),
            attempts=InMemoryAttempts(self.attempts),
            clock=self.clock,
            event_log=rig.publisher,
            run_source=rig.clients.profile,
            **options,
        )
        service.rig = rig  # type: ignore[attr-defined]
        self.replicas.append(service)
        return service

    async def crash(self, service: RunService, *, lapse: bool = True) -> None:
        """Kill the process: tasks stop, nothing is released, no shutdown code runs.

        ``lapse`` lets the dead replica's leases run out before the replacement starts (a
        Kubernetes restart takes longer than the 60 s lease TTL only sometimes: both are tested).
        """
        if lapse:
            self.clock.now += 61
        tasks = [t for run in service._runs.values() for t in (run.task, run.expiry) if t]
        if service._heartbeat is not None:
            tasks.append(service._heartbeat)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        for service in self.replicas:
            await service.aclose()


@pytest.fixture
async def cluster() -> Any:
    made: list[Cluster] = []

    def make(world: FakeWorld | None = None, **options: Any) -> Cluster:
        made.append(Cluster(world, **options))
        return made[-1]

    yield make
    for c in made:
        await c.close()


def body(**input_over: Any) -> StartRun:
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


def asking_world() -> FakeWorld:
    world = FakeWorld()
    world.voi_fn = lambda b: voi_response(True)
    world.verdict_fn = lambda req: fake_verdict(
        req, "NO-GO" if req["gate_inputs"]["estimated_effort_hours"] > 150 else "GO"
    )
    return world


def approval_world() -> FakeWorld:
    world = FakeWorld()
    world.evidence = world.evidence[:1]
    world.needs_approval = True
    return world


class Blocker:
    """A ``before`` hook that holds calls to ``route`` open until ``gate`` is set."""

    def __init__(self, route: str, *, only_first: bool = True) -> None:
        self.route, self.only_first = route, only_first
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.count = 0

    async def __call__(self, service: str, call: str) -> None:
        if call != self.route or (self.only_first and self.count):
            return
        self.count += 1
        self.entered.set()
        await self.gate.wait()


async def paused_run(replica: RunService, **input_over: Any) -> str:
    run_id = await replica.start(body(**input_over))
    await replica.wait(run_id)
    return run_id


def steps_of(replica: RunService, run_id: str) -> Any:
    return replica._runs[run_id].ctx.ledger.spent()


# ---- paused runs ---------------------------------------------------------------------------------
async def test_a_run_waiting_for_an_answer_survives_a_restart_and_finishes_when_answered(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    question = (await before.get(run_id)).pending_question
    assert question is not None
    searches = c.world.count("kb", "POST /search")
    await c.crash(before)

    after = c.replica("b")
    assert await after.recover() == {"paused": 1}
    view = await after.get(run_id)
    assert view.state == "awaiting_input"
    assert view.pending_question == question
    await after.answer(run_id, question["question_id"], 300)  # too many hours: NO-GO
    await after.wait(run_id)
    done = await after.get(run_id)
    assert done.state == "succeeded"
    assert done.result is not None and done.result["verdict"] == "NO-GO"
    assert c.world.count("kb", "POST /search") == searches  # nothing before the pause was redone


async def scouts_in_an_uninterrupted_run() -> int:
    c = Cluster(approval_world())
    service = c.replica("solo")
    run_id = await paused_run(service)
    approval = (await service.get(run_id)).pending_approval
    assert approval is not None
    await service.approve(run_id, approval["approval_id"], "approve")
    await service.wait(run_id)
    await c.close()
    return c.world.count("scouts", "POST /scout")


async def test_a_run_waiting_for_an_approval_survives_a_restart_and_can_be_approved(
    cluster: Any,
) -> None:
    c = cluster(approval_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    approval = (await before.get(run_id)).pending_approval
    assert approval is not None
    await c.crash(before)

    after = c.replica("b")
    assert await after.recover() == {"paused": 1}
    view = await after.get(run_id)
    assert (view.state, view.pending_approval) == ("awaiting_approval", approval)
    await after.approve(run_id, approval["approval_id"], "approve")
    await after.wait(run_id)
    assert (await after.get(run_id)).state == "succeeded"
    assert c.world.count("scouts", "POST /scout") == await scouts_in_an_uninterrupted_run()


async def test_a_declined_approval_after_a_restart_skips_scouting(cluster: Any) -> None:
    c = cluster(approval_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    approval = (await before.get(run_id)).pending_approval
    assert approval is not None
    await c.crash(before)
    after = c.replica("b")
    await after.recover()
    await after.approve(run_id, approval["approval_id"], "decline")
    await after.wait(run_id)
    assert c.world.count("scouts", "POST /scout") == 0


async def test_a_wrong_or_stale_question_id_is_still_refused_after_a_restart(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    await c.crash(before)
    after = c.replica("b")
    await after.recover()
    with pytest.raises(Conflict):
        await after.answer(run_id, "q_stale", 10)
    with pytest.raises(Conflict):
        await after.approve(run_id, "ap_x", "approve")
    assert (await after.get(run_id)).state == "awaiting_input"


async def test_a_recovered_question_still_expires_and_falls_back(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    await c.crash(before)
    after = c.replica("b", question_timeout_s=0.05)
    await after.recover()
    await after.wait(run_id)
    await asyncio.sleep(0.2)
    await after.wait(run_id)
    assert (await after.get(run_id)).state == "succeeded"


async def test_the_recovered_run_reports_its_spend_and_budget(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    spent = (await before.get(run_id)).spent
    await c.crash(before)
    after = c.replica("b")
    await after.recover()
    view = await after.get(run_id)
    assert view.spent == spent and view.spent["steps"] > 0
    assert view.budget["max_steps"] == 80


# ---- running runs --------------------------------------------------------------------------------
async def baseline_steps() -> int:
    c = Cluster(asking_world())
    service = c.replica("solo")
    run_id = await service.start(body())
    await service.wait(run_id)
    question = (await service.get(run_id)).pending_question
    assert question is not None
    await service.answer(run_id, question["question_id"], 100)
    await service.wait(run_id)
    steps = int((await service.get(run_id)).spent["steps"])
    await c.close()
    return steps


async def test_a_run_killed_mid_step_resumes_once_and_is_not_billed_twice(cluster: Any) -> None:
    c = cluster(asking_world())
    hook = Blocker("POST /score-all")
    c.world.before = hook
    before = c.replica("a")
    run_id = await before.start(body())
    await asyncio.wait_for(hook.entered.wait(), 5)  # the scorers are in flight
    await c.crash(before)
    c.world.before = None

    after = c.replica("b")
    c.clock.now += 61  # a's lease lapses: nobody renewed it
    assert await after.recover() == {"resumed": 1}
    await after.wait(run_id)
    question = (await after.get(run_id)).pending_question
    assert question is not None  # the scorers ran after the restart and the run went on
    await after.answer(run_id, question["question_id"], 100)
    await after.wait(run_id)
    view = await after.get(run_id)
    assert view.state == "succeeded"
    assert view.spent["steps"] == await baseline_steps()  # the step cut off by the crash: once
    assert c.world.count("scouts", "POST /scout") == 0
    assert c.world.count("validators", "POST /score-all") == 1  # the cut-off call never answered


async def test_a_resumed_run_does_not_redo_the_steps_that_finished_before_the_crash(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    hook = Blocker("POST /score-all")
    c.world.before = hook
    before = c.replica("a")
    run_id = await before.start(body())
    await asyncio.wait_for(hook.entered.wait(), 5)
    searches, features = (
        c.world.count("kb", "POST /search"),
        c.world.count("analyst", "POST /features"),
    )
    await c.crash(before)
    c.world.before = None
    c.clock.now += 61
    after = c.replica("b")
    await after.recover()
    await after.wait(run_id)
    assert c.world.count("kb", "POST /search") == searches
    assert c.world.count("analyst", "POST /features") == features
    assert run_id in after._runs


async def test_the_events_of_a_recovered_run_continue_the_sequence(cluster: Any) -> None:
    c = cluster(asking_world())
    hook = Blocker("POST /score-all")
    c.world.before = hook
    before = c.replica("a")
    run_id = await before.start(body())
    await asyncio.wait_for(hook.entered.wait(), 5)
    last = await before.rig.publisher.last_seq(run_id)
    await c.crash(before)
    c.world.before = None
    c.clock.now += 61
    after = c.replica("b")
    await after.recover()
    await after.wait(run_id)
    seqs = [e.seq for e in await after.events(run_id, 0)]
    assert seqs == sorted(set(seqs)) and seqs[-1] > last  # strictly increasing, no repeats
    assert [e.seq for e in await after.events(run_id, last)] == seqs[seqs.index(last) + 1 :]


async def test_the_ledger_is_restored_when_redis_lost_it(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    spent = (await before.get(run_id)).spent
    await c.crash(before)
    after = c.replica("b", fresh_redis=True)  # a Redis that forgot everything
    await after.recover()
    assert (await after.get(run_id)).spent == spent


async def test_a_ledger_that_survived_is_not_overwritten_by_the_mirror(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    await before._runs[run_id].ctx.ledger.record(Spend(steps=5))  # M5 charged after the mirror
    spent = (await before.get(run_id)).spent
    await c.crash(before)
    after = c.replica("b")
    await after.recover()
    assert (await after.get(run_id)).spent == spent


# ---- runs that cannot be recovered ---------------------------------------------------------------
async def test_a_run_without_a_checkpoint_is_failed_with_a_fixed_reason(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_ghost"] = {
        "run_id": "run_ghost",
        "user_id": "u1",
        "kind": "validate",
        "state": "running",
        "input": {"idea_text": "x"},
        "context": {},
        "pending_question": None,
    }
    service = c.replica("b")
    assert await service.recover() == {"failed": 1}
    view = await service.get("run_ghost")
    assert (view.state, view.error) == ("failed", RECOVERY_FAILED)
    assert c.world.runs["run_ghost"]["state"] == "failed"  # M2 is corrected too
    assert c.world.runs["run_ghost"]["context"]["error"] == RECOVERY_FAILED
    assert [e.type for e in await service.events("run_ghost", 0)] == ["run_failed"]


def corrupt(saver: MemorySaver, thread: str | None = None) -> None:
    """Overwrite the stored state values (of one run, or all) with bytes msgpack cannot read."""
    for key in list(saver.blobs):
        if thread is None or key[0] == thread:
            saver.blobs[key] = (saver.blobs[key][0], b"\xc1garbage")


async def test_a_corrupt_checkpoint_fails_the_run_instead_of_crashing_recovery(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    broken = await paused_run(before)
    await c.crash(before)
    corrupt(c.saver)
    after = c.replica("b")
    assert await after.recover() == {"failed": 1}
    view = await after.get(broken)
    assert (view.state, view.error) == ("failed", RECOVERY_FAILED)
    assert "msgpack" not in (view.error or "")  # internals never leak


async def test_one_corrupt_run_does_not_stop_the_others_from_recovering(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    good = await paused_run(before, idea_id="idea_good")
    bad = await paused_run(before, idea_id="idea_bad")
    await c.crash(before)
    corrupt(c.saver, bad)
    after = c.replica("b")
    assert await after.recover() == {"paused": 1, "failed": 1}
    assert (await after.get(good)).state == "awaiting_input"
    assert (await after.get(bad)).state == "failed"


async def test_a_checkpoint_that_belongs_to_another_run_is_refused(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    one = await paused_run(before, idea_id="idea_1")
    other = await paused_run(before, idea_id="idea_2")
    await c.crash(before)
    for key in [k for k in c.saver.blobs if k[0] == other and k[2] == "run_id"]:
        c.saver.blobs[key] = c.saver.serde.dumps_typed(one)  # `other` now claims to be `one`
    after = c.replica("b")
    outcomes = await after.recover()
    assert outcomes.get("failed") == 1 and outcomes.get("paused") == 1


async def test_an_unknown_run_kind_in_the_mirror_is_failed_not_fatal(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_odd"] = {
        "run_id": "run_odd",
        "user_id": "u1",
        "kind": "eval",
        "state": "running",
        "input": {},
        "context": {},
        "pending_question": None,
    }
    service = c.replica("b")
    assert await service.recover() == {"failed": 1}
    assert c.world.runs["run_odd"]["state"] == "failed"


async def test_finished_and_cancelled_runs_are_not_recovered(cluster: Any) -> None:
    c = cluster()
    for n, state in enumerate(["succeeded", "failed", "budget_stopped", "cancelled"]):
        c.world.runs[f"run_{n}"] = {
            "run_id": f"run_{n}",
            "user_id": "u1",
            "kind": "validate",
            "state": state,
            "input": {},
            "context": {},
            "pending_question": None,
        }
    service = c.replica("b")
    assert await service.recover() == {}
    assert c.world.count("profile", "GET /internal/runs") == 1


async def test_a_run_whose_graph_finished_before_the_crash_is_settled_not_rerun(
    cluster: Any,
) -> None:
    c = cluster()
    before = c.replica("a")
    run_id = await before.start(body())
    await before.wait(run_id)
    result = (await before.get(run_id)).result
    c.world.runs[run_id]["state"] = "running"  # the final mirror write never reached M2
    searches = c.world.count("kb", "POST /search")
    await c.crash(before)
    after = c.replica("b")
    assert await after.recover() == {"finished": 1}
    view = await after.get(run_id)
    assert (view.state, view.result) == ("succeeded", result)
    assert c.world.runs[run_id]["state"] == "succeeded"
    assert c.world.count("kb", "POST /search") == searches


# ---- single flight, leases ----------------------------------------------------------------------
async def test_two_replicas_racing_to_recover_resume_each_run_exactly_once(cluster: Any) -> None:
    c = cluster(asking_world())
    hook = Blocker("POST /score-all")
    c.world.before = hook
    dead = c.replica("dead")
    running = await dead.start(body(idea_id="idea_run"))
    await asyncio.wait_for(hook.entered.wait(), 5)
    c.world.before = None
    c.world.voi_fn = lambda b: voi_response(True)
    paused = await dead.start(body(idea_id="idea_paused"))
    await dead.wait(paused)
    await c.crash(dead)
    c.clock.now += 61
    scored = c.world.count("validators", "POST /score-all")

    a, b = c.replica("a"), c.replica("b")
    first, second = await asyncio.gather(a.recover(), b.recover())
    merged: dict[str, int] = {}
    for outcome in (first, second):
        for key, count in outcome.items():
            merged[key] = merged.get(key, 0) + count
    assert merged["resumed"] == 1 and merged["paused"] == 1  # one owner per run
    assert merged.get("failed", 0) == 0
    owners = [s for s in (a, b) if running in s._runs]
    assert len(owners) == 1
    await owners[0].wait(running)
    assert c.world.count("validators", "POST /score-all") == scored + 1  # re-run exactly once


async def test_recovery_leaves_a_run_alone_while_another_replica_holds_its_lease(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    live = c.replica("live")
    run_id = await paused_run(live)
    other = c.replica("other")
    assert await other.recover() == {"leased_elsewhere": 1}
    assert run_id not in other._runs
    assert await live.recover() == {"known": 1}  # asking again is a no-op


async def test_an_orphan_is_adopted_after_its_owner_s_lease_lapses_and_not_before(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    dead = c.replica("dead")
    run_id = await paused_run(dead)
    await c.crash(dead, lapse=False)
    survivor = c.replica("survivor")
    c.clock.now += 30
    assert await survivor.recover() == {"leased_elsewhere": 1}
    c.clock.now += 31
    assert await survivor.recover() == {"paused": 1}
    assert (await survivor.get(run_id)).state == "awaiting_input"


async def test_a_graceful_shutdown_releases_leases_so_the_next_replica_adopts_at_once(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    old = c.replica("old")
    await paused_run(old)
    await old.aclose()
    assert await c.replica("new").recover() == {"paused": 1}


async def test_a_finished_run_gives_up_its_lease(cluster: Any) -> None:
    c = cluster()
    service = c.replica("a")
    run_id = await service.start(body())
    await service.wait(run_id)
    assert f"run:{run_id}:lease" not in c.leases and run_id not in c.leases


async def test_a_replica_that_loses_a_lease_stops_driving_the_run(cluster: Any) -> None:
    c = cluster(asking_world())
    service = c.replica("a", heartbeat_s=0.01)
    run_id = await paused_run(service)
    c.leases[run_id] = ("thief", c.clock.now + 60)  # another replica took it over
    for _ in range(100):
        if run_id not in service._runs:
            break
        await asyncio.sleep(0.01)
    assert run_id not in service._runs


# ---- bounded concurrency and stress -------------------------------------------------------------
async def test_fifty_paused_runs_are_all_recovered_and_can_all_be_answered(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    ids = [await paused_run(before, idea_id=f"idea_{n}") for n in range(50)]
    questions = {i: (await before.get(i)).pending_question for i in ids}
    await c.crash(before)
    c.clock.now += 61

    after = c.replica("b", resume_concurrency=4)
    assert await after.recover() == {"paused": 50}
    for run_id in ids:
        view = await after.get(run_id)
        assert view.state == "awaiting_input" and view.pending_question == questions[run_id]
    for run_id in ids:
        question = questions[run_id]
        assert question is not None
        await after.answer(run_id, question["question_id"], 50)
    await asyncio.gather(*(after.wait(i) for i in ids))
    assert {(await after.get(i)).state for i in ids} == {"succeeded"}


async def test_recovered_running_runs_resume_with_bounded_concurrency(cluster: Any) -> None:
    c = cluster()
    hook = Blocker("POST /score-all", only_first=False)
    c.world.before = hook
    before = c.replica("a")
    ids = [await before.start(body(idea_id=f"idea_{n}")) for n in range(12)]
    for _ in range(200):
        if hook.count == 12:
            break
        await asyncio.sleep(0.01)
    assert hook.count == 12  # all twelve are mid-step
    await c.crash(before)
    in_flight = {"now": 0, "max": 0}

    async def meter(service: str, call: str) -> None:
        if call == "POST /score-all":
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
            await asyncio.sleep(0.02)
            in_flight["now"] -= 1

    c.world.before = meter
    c.clock.now += 61
    after = c.replica("b", resume_concurrency=3)
    outcomes = await after.recover()
    assert outcomes == {"resumed": 12}
    await asyncio.gather(*(after.wait(i) for i in ids))
    assert {(await after.get(i)).state for i in ids} == {"succeeded"}
    assert 1 <= in_flight["max"] <= 3


async def test_resume_concurrency_must_be_positive(cluster: Any) -> None:
    c = cluster()
    with pytest.raises(ValueError):
        c.replica("a", resume_concurrency=0)


# ---- listing M2 -----------------------------------------------------------------------------------
async def test_recovery_pages_through_more_runs_than_one_page(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    ids = [await paused_run(before, idea_id=f"idea_{n}") for n in range(7)]
    await c.crash(before)
    c.clock.now += 61
    after = c.replica("b", recovery_page_size=3)
    assert await after.recover() == {"paused": 7}
    assert c.world.count("profile", "GET /internal/runs") == 3
    assert set(ids) <= set(after._runs)


async def test_recovery_asks_m2_only_for_runs_that_are_not_finished(cluster: Any) -> None:
    c = cluster()
    await c.replica("a").recover()
    wanted = c.world.last_query["state"].split(",")
    assert set(wanted) == {"created", "running", "awaiting_input", "awaiting_approval"}


async def test_an_m2_outage_fails_the_pass_and_the_loop_retries(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    await c.crash(before)
    c.clock.now += 61
    c.world.faults[("profile", "GET /internal/runs")] = [503] * 12
    after = c.replica("b")
    with pytest.raises(UpstreamError):
        await after.recover()
    c.world.faults[("profile", "GET /internal/runs")] = [503] * 12
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            c.world.faults.clear()  # M2 is back after two waits

    await recovery_loop(
        after, interval_s=0, attempts=5, retry_delay_s=1.0, sleep=fake_sleep, rounds=1
    )
    assert (await after.get(run_id)).state == "awaiting_input"
    assert sleeps[:2] == [1.0, 2.0]  # backs off


async def test_the_recovery_loop_gives_up_after_its_attempts_without_raising(
    cluster: Any,
) -> None:
    c = cluster()
    c.world.faults[("profile", "GET /internal/runs")] = [503] * 100
    after = c.replica("b")

    async def no_sleep(seconds: float) -> None:
        return None

    await recovery_loop(
        after, interval_s=0, attempts=2, retry_delay_s=0.0, sleep=no_sleep, rounds=1
    )


async def test_the_recovery_loop_sweeps_again_to_adopt_orphans_of_a_dead_replica(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    dead = c.replica("dead")
    run_id = await paused_run(dead)
    await c.crash(dead, lapse=False)
    survivor = c.replica("survivor")
    ticks = {"n": 0}

    async def tick(seconds: float) -> None:
        ticks["n"] += 1
        c.clock.now += 61  # a sweep interval later the dead replica's lease has lapsed

    await recovery_loop(survivor, interval_s=30, attempts=1, retry_delay_s=0, sleep=tick, rounds=2)
    assert (await survivor.get(run_id)).state == "awaiting_input"


# ---- the M2 mirror -------------------------------------------------------------------------------
async def test_the_mirror_uses_m2_s_run_shape(cluster: Any) -> None:
    c = cluster(asking_world())
    service = c.replica("a")
    run_id = await paused_run(service, idea_id="idea_shape")
    record = c.world.runs[run_id]
    assert set(record) <= {
        "run_id",
        "user_id",
        "kind",
        "state",
        "input",
        "context",
        "pending_question",
    }
    assert record["state"] == RunStatus.AWAITING_INPUT.value
    assert record["input"]["idea_id"] == "idea_shape"
    assert record["pending_question"]["question_id"]
    assert set(record["context"]) == {
        "step",
        "spent",
        "cost_bonus_usd",
        "pending_approval",
        "result",
        "error",
    }
    assert record["context"]["spent"]["steps"] > 0


# ---- hardening: poison runs, tampered checkpoints, split brain, leaks ----------------------------
class StubGraph:
    """Stands in for the compiled graph when a test needs a specific checkpoint snapshot."""

    def __init__(
        self, values: Any, tasks: tuple[Any, ...] = (), next_: tuple[str, ...] = ()
    ) -> None:
        self.snapshot = SimpleNamespace(values=values, tasks=tasks, next=next_)

    async def aget_state(self, config: Any) -> Any:
        return self.snapshot


def stub_checkpoint(service: RunService, values: Any, **kw: Any) -> None:
    graph = StubGraph(values, **kw)
    service._graph_for = lambda kind, ctx: graph  # type: ignore[method-assign, assignment, return-value]


_construct: Any = ValidateState.model_construct  # skips validation, like a bad checkpoint


def mirror_row(run_id: str, **over: Any) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "user_id": "u1",
        "kind": "validate",
        "state": "running",
        "input": {"idea_text": "x"},
        "context": {},
        "pending_question": None,
        **over,
    }


def valid_state(run_id: str, **over: Any) -> ValidateState:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "user_id": "u1",
        "idea_id": "i1",
        "idea_text": "x",
        "status": RunStatus.SUCCEEDED,
        "card": {"verdict": "GO"},
        **over,
    }
    return ValidateState.model_validate(fields)


async def test_a_run_that_keeps_killing_its_process_is_given_up_on(cluster: Any) -> None:
    c = cluster(asking_world(), limits={"max_steps": 500})
    hook = Blocker("POST /score-all", only_first=False)
    c.world.before = hook
    first = c.replica("r0")
    run_id = await first.start(body())
    await asyncio.wait_for(hook.entered.wait(), 5)
    await c.crash(first)
    outcomes = []
    for n in range(1, 4):  # every replica resumes it and dies on it again
        replica = c.replica(f"r{n}", recovery_attempt_limit=2)
        outcomes.append(await replica.recover())
        await asyncio.sleep(0.05)
        await c.crash(replica)
    assert outcomes == [{"resumed": 1}, {"resumed": 1}, {"failed": 1}]
    last = c.replica("final")
    assert await last.recover() == {}  # failed in M2: no longer listed
    assert c.world.runs[run_id]["state"] == "failed"
    assert c.world.runs[run_id]["context"]["error"] == RECOVERY_FAILED


async def test_a_recovered_run_that_pauses_safely_gets_its_attempts_back(cluster: Any) -> None:
    c = cluster(asking_world())
    service = c.replica("r0")
    run_id = await paused_run(service)
    for n in range(1, 6):  # far more restarts than the limit, each one safe
        await c.crash(service)
        service = c.replica(f"r{n}", recovery_attempt_limit=2)
        assert await service.recover() == {"paused": 1}
    assert (await service.get(run_id)).state == "awaiting_input"


async def test_a_run_that_finishes_after_recovery_clears_its_attempts(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    await c.crash(before)
    after = c.replica("b")
    await after.recover()
    question = (await after.get(run_id)).pending_question
    assert question is not None
    await after.answer(run_id, question["question_id"], 50)
    await after.wait(run_id)
    assert c.attempts.get(run_id) is None


@pytest.mark.parametrize(
    "state",
    [
        {"status": RunStatus.RUNNING},  # not terminal, yet nothing left to run
        {"status": RunStatus.CREATED},
        {"status": RunStatus.AWAITING_INPUT},
        {"status": RunStatus.SUCCEEDED, "card": None},  # "succeeded" with no result
        {"status": RunStatus.FAILED, "card": None},
    ],
)
async def test_a_finished_looking_checkpoint_must_be_terminal_and_hold_a_card(
    cluster: Any, state: dict[str, Any]
) -> None:
    c = cluster()
    c.world.runs["run_x"] = mirror_row("run_x")
    service = c.replica("b")
    stub_checkpoint(service, valid_state("run_x", **state))
    assert await service.recover() == {"failed": 1}
    view = await service.get("run_x")
    assert (view.state, view.result, view.error) == ("failed", None, RECOVERY_FAILED)


async def test_a_terminal_checkpoint_with_a_card_is_settled(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_x"] = mirror_row("run_x")
    service = c.replica("b")
    stub_checkpoint(service, valid_state("run_x"))
    assert await service.recover() == {"finished": 1}
    view = await service.get("run_x")
    assert (view.state, view.result) == ("succeeded", {"verdict": "GO"})


async def test_a_discover_run_is_settled_without_a_validate_card_rule(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_d"] = mirror_row("run_d", kind="discover", input={"niche": "x", "count": 1})
    service = c.replica("b")
    state = DiscoverState(
        run_id="run_d",
        user_id="u1",
        niche="x",
        count=1,
        status=RunStatus.SUCCEEDED,
        card={"ideas": []},
    )
    stub_checkpoint(service, state)
    assert await service.recover() == {"finished": 1}


async def test_a_state_built_without_validation_is_revalidated_and_refused(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_x"] = mirror_row("run_x")
    service = c.replica("b")
    tampered = _construct(  # langgraph falls back to this on a bad checkpoint
        run_id="run_x",
        user_id="u1",
        idea_id=12345,
        idea_text=None,
        status=RunStatus.SUCCEEDED,
        card={"verdict": "GO"},
    )
    stub_checkpoint(service, tampered)
    assert await service.recover() == {"failed": 1}
    assert (await service.get("run_x")).error == RECOVERY_FAILED


async def test_a_construct_state_missing_required_fields_is_refused(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_x"] = mirror_row("run_x")
    service = c.replica("b")
    stub_checkpoint(service, _construct(run_id="run_x", user_id="u1"))
    assert await service.recover() == {"failed": 1}


async def test_a_checkpoint_for_another_user_is_refused(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_x"] = mirror_row("run_x", user_id="owner")
    service = c.replica("b")
    stub_checkpoint(service, valid_state("run_x", user_id="someone-else"))
    assert await service.recover() == {"failed": 1}
    assert c.world.runs["run_x"]["user_id"] == "owner"


async def test_a_failed_persist_while_pausing_leaves_no_timer_and_no_owner(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    await c.crash(before)
    after = c.replica("b", question_timeout_s=0.05)

    async def boom(run: Any) -> None:
        raise ConnectionError("redis is down")

    after._persist = boom
    assert await after.recover() == {"error": 1}
    assert run_id not in after._runs  # no half-restored run
    assert not [t for t in asyncio.all_tasks() if "_expire" in repr(t.get_coro())]
    await asyncio.sleep(0.15)  # a timer that survived would have resumed the run by now
    assert c.world.count("verdict", "POST /verdict") == 0
    assert f"run:{run_id}:lease" not in c.leases and run_id not in c.leases  # free to retry


async def test_the_error_path_stops_a_resumed_task(cluster: Any) -> None:
    c = cluster(asking_world())
    service = c.replica("b")
    c.world.runs["run_x"] = mirror_row("run_x")
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(60)

    run_state = valid_state("run_x", status=RunStatus.RUNNING, card=None)
    stub_checkpoint(service, run_state, next_=("scorers",))
    service._drive_recovered = lambda run: forever()
    assert await service.recover() == {"resumed": 1}
    await asyncio.wait_for(started.wait(), 5)
    task = service._runs["run_x"].task
    assert task is not None and not task.done()
    service._discard(service._runs["run_x"])
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()
    assert "run_x" not in service._runs


async def test_a_single_renew_failure_does_not_abandon_a_run_but_two_in_a_row_do(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    service = c.replica("a", heartbeat_s=0.01, lease_failures_allowed=2)
    run_id = await paused_run(service)
    real = service._leases
    assert real is not None
    calls = {"n": 0}

    class Flaky:
        async def acquire(self, rid: str) -> bool:
            return bool(await real.acquire(rid))

        async def release(self, rid: str) -> None:
            await real.release(rid)

        async def renew(self, rid: str) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("blip")
            if calls["n"] < 5:
                return bool(await real.renew(rid))
            raise ConnectionError("redis is down")

    service._leases = Flaky()
    for _ in range(300):
        if run_id not in service._runs:
            break
        await asyncio.sleep(0.01)
    assert calls["n"] >= 6 and run_id not in service._runs  # survived the blip, not the outage


async def test_the_heartbeat_loop_survives_an_unexpected_error(cluster: Any) -> None:
    c = cluster(asking_world())
    service = c.replica("a", heartbeat_s=0.01)
    await paused_run(service)
    calls = {"n": 0}
    original = service._abandon

    async def bad(run: Any) -> None:
        calls["n"] += 1
        raise RuntimeError("boom")

    service._abandon = bad
    c.leases.clear()  # every renew now reports the lease as lost -> _abandon raises
    await asyncio.sleep(0.15)
    assert calls["n"] >= 2  # it kept trying: the loop did not die
    assert service._heartbeat is not None and not service._heartbeat.done()
    service._abandon = original


async def test_an_unusable_budget_mirror_fails_the_run_instead_of_resetting_spend(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    c.world.runs[run_id]["context"]["spent"] = {"steps": "lots"}
    await c.crash(before)
    after = c.replica("b", fresh_redis=True)
    assert await after.recover() == {"failed": 1}


async def test_a_mirror_without_spend_is_a_run_that_had_none(cluster: Any) -> None:
    c = cluster()
    c.world.runs["run_x"] = mirror_row("run_x")
    service = c.replica("b")
    stub_checkpoint(service, valid_state("run_x"))
    assert await service.recover() == {"finished": 1}
    assert (await service.get("run_x")).spent["steps"] == 0


async def test_an_approved_cost_raise_survives_a_redis_loss(cluster: Any) -> None:
    c = cluster(asking_world())
    before = c.replica("a")
    run_id = await paused_run(before)
    run = before._runs[run_id]
    await run.ctx.ledger.raise_cost_limit(0.01)
    await before._persist(run)
    raised = (await before.get(run_id)).budget["max_cost_usd"]
    await c.crash(before)
    after = c.replica("b", fresh_redis=True)
    await after.recover()
    assert (await after.get(run_id)).budget["max_cost_usd"] == pytest.approx(raised)
    assert c.world.runs[run_id]["context"]["cost_bonus_usd"] == pytest.approx(0.01)


async def test_an_oversized_result_is_left_out_of_the_mirror_not_sent_to_be_refused(
    cluster: Any,
) -> None:
    c = cluster(asking_world())
    service = c.replica("a")
    run_id = await paused_run(service)
    run = service._runs[run_id]
    run.result = {"blob": "x" * 70_000}
    await service._persist(run)
    context = c.world.runs[run_id]["context"]
    assert context["result"] is None and context["result_truncated"] is True
    assert len(json.dumps(context)) < 64 * 1024
    assert (await service.get(run_id)).result == run.result  # the live run still has it


async def test_finished_runs_leave_memory_after_their_ttl(cluster: Any) -> None:
    c = cluster()
    service = c.replica("a", finished_ttl_s=3600)
    done = await service.start(body())
    await service.wait(done)
    c.clock.now += 3599
    other = await service.start(body(idea_id="idea_2"))
    await service.wait(other)
    assert done in service._runs  # not yet
    c.clock.now += 2
    third = await service.start(body(idea_id="idea_3"))
    await service.wait(third)
    assert done not in service._runs and other in service._runs
    with pytest.raises(NotFound):
        await service.get(done)


async def test_a_paused_run_is_never_evicted(cluster: Any) -> None:
    c = cluster(asking_world())
    service = c.replica("a", finished_ttl_s=1)
    run_id = await paused_run(service)
    c.clock.now += 10_000
    await service.recover()
    other = await service.start(body(idea_id="idea_2"))
    await service.wait(other)
    assert run_id in service._runs


async def test_a_crashing_run_does_not_log_the_idea_text(
    cluster: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "MY-SECRET-STARTUP-IDEA-7731"

    async def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise ValueError(f"bad input {secret}")

    monkeypatch.setattr(CompiledStateGraph, "ainvoke", boom)
    c = cluster()
    service = c.replica("a")
    run_id = await service.start(body(idea_text=f"{secret} a CLI"))
    await service.wait(run_id)
    out = capsys.readouterr()
    assert "run_crashed" in out.out + out.err
    assert secret not in out.out + out.err
    view = await service.get(run_id)
    assert (view.state, view.error) == ("failed", "internal error")
