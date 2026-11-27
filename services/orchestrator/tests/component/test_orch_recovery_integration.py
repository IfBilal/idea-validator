"""Recovery on the real stack: Postgres checkpoints, Redis leases, events and budget ledger."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

from svc_orchestrator.config import Settings
from svc_orchestrator.main import build_app
from svc_orchestrator.models.api import StartRun
from svc_orchestrator.repositories.attempts import RedisAttempts
from svc_orchestrator.repositories.checkpoints import postgres_saver, psycopg_url
from svc_orchestrator.repositories.events import RedisPublisher, RunEmitter
from svc_orchestrator.repositories.leases import RedisLeases
from svc_orchestrator.services.runs import RECOVERY_FAILED, RunService
from svc_orchestrator.testing import FakeWorld, Rig, fake_verdict, voi_response

pytestmark = pytest.mark.integration
CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"

BODY = StartRun.model_validate(
    {
        "kind": "validate",
        "user_id": "u1",
        "input": {"idea_text": "A CLI that lints Dockerfiles", "idea_id": "idea_1", "version": 1},
    }
)


def asking_world() -> FakeWorld:
    world = FakeWorld()
    world.voi_fn = lambda b: voi_response(True)
    world.verdict_fn = lambda req: fake_verdict(
        req, "NO-GO" if req["gate_inputs"]["estimated_effort_hours"] > 150 else "GO"
    )
    return world


class Stack:
    """Real Postgres and Redis shared by every replica of a test."""

    def __init__(self, world: FakeWorld, database_url: str, redis: Redis, stack: AsyncExitStack):
        self.world, self.database_url, self.redis, self.stack = world, database_url, redis, stack
        self.replicas: list[RunService] = []

    async def replica(self, owner: str, **options: Any) -> RunService:
        saver = await self.stack.enter_async_context(postgres_saver(self.database_url))
        rig = Rig(self.world)
        rig.redis = self.redis  # type: ignore[assignment]
        rig.publisher = RedisPublisher(self.redis)  # type: ignore[assignment]
        service = RunService(
            context_factory=lambda run_id, kind: rig.context(run_id, kind, owner=owner),
            checkpointer=saver,
            config=rig.config,
            leases=RedisLeases(self.redis, owner, ttl_s=60),
            attempts=RedisAttempts(self.redis),
            event_log=rig.publisher,
            run_source=rig.clients.profile,
            **options,
        )
        service.rig = rig  # type: ignore[attr-defined]
        self.replicas.append(service)
        return service

    async def crash(self, service: RunService) -> None:
        """Stop the process's tasks and forget its leases' owner by deleting them (TTL elapsed)."""
        tasks = [t for r in service._runs.values() for t in (r.task, r.expiry) if t]
        if service._heartbeat:
            tasks.append(service._heartbeat)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for run_id in service._runs:
            await self.redis.delete(f"run:{run_id}:lease")


@pytest.fixture
async def stack(postgres_url: str, redis_url: str) -> AsyncIterator[Stack]:
    redis = Redis.from_url(redis_url)
    await redis.flushdb()
    async with AsyncExitStack() as exit_stack:
        s = Stack(asking_world(), postgres_url, redis, exit_stack)
        yield s
        for service in s.replicas:
            await service.aclose()
    await redis.aclose()


async def paused(service: RunService, **over: Any) -> str:
    body = StartRun.model_validate(
        {
            "kind": "validate",
            "user_id": "u1",
            "input": {
                "idea_text": "A CLI that lints Dockerfiles",
                "idea_id": over.get("idea_id", "idea_1"),
                "version": 1,
            },
        }
    )
    run_id = await service.start(body)
    await service.wait(run_id)
    return run_id


async def test_a_paused_run_survives_a_restart_on_postgres_and_redis(stack: Stack) -> None:
    before = await stack.replica("a")
    run_id = await paused(before)
    question = (await before.get(run_id)).pending_question
    assert question is not None
    seen = [e.seq for e in await before.events(run_id, 0)]
    assert seen and seen == sorted(seen)
    await stack.crash(before)

    after = await stack.replica("b")
    assert await after.recover() == {"paused": 1}
    assert (await after.get(run_id)).pending_question == question
    await after.answer(run_id, question["question_id"], 300)
    await after.wait(run_id)
    done = await after.get(run_id)
    assert done.result is not None and done.result["verdict"] == "NO-GO"
    stored = [e.seq for e in await after.events(run_id, 0)]
    assert stored[: len(seen)] == seen  # replay returns what was stored before the restart
    assert stored == list(range(1, len(stored) + 1))  # and the sequence went on without repeats
    assert stored[-1] > seen[-1]


async def test_a_run_cut_off_mid_step_resumes_from_its_postgres_checkpoint(stack: Stack) -> None:
    entered, gate = asyncio.Event(), asyncio.Event()

    async def hold(service: str, call: str) -> None:
        if call == "POST /score-all" and not entered.is_set():
            entered.set()
            await gate.wait()

    stack.world.before = hold
    before = await stack.replica("a")
    run_id = await before.start(BODY)
    await asyncio.wait_for(entered.wait(), 5)
    await stack.crash(before)
    stack.world.before = None

    after = await stack.replica("b")
    assert await after.recover() == {"resumed": 1}
    await after.wait(run_id)
    question = (await after.get(run_id)).pending_question
    assert question is not None
    await after.answer(run_id, question["question_id"], 100)
    await after.wait(run_id)
    assert (await after.get(run_id)).state == "succeeded"
    assert stack.world.count("kb", "POST /search") == 4  # evidence was not fetched again


async def test_a_corrupt_postgres_checkpoint_fails_the_run(stack: Stack, postgres_url: str) -> None:
    before = await stack.replica("a")
    run_id = await paused(before)
    await stack.crash(before)
    async with await psycopg.AsyncConnection.connect(
        psycopg_url(postgres_url), autocommit=True
    ) as conn:
        await conn.execute(
            "UPDATE orchestrator.checkpoint_blobs SET blob = %s WHERE thread_id = %s",
            (b"\xc1garbage", run_id),
        )
    after = await stack.replica("b")
    assert await after.recover() == {"failed": 1}
    view = await after.get(run_id)
    assert (view.state, view.error) == ("failed", RECOVERY_FAILED)


async def test_two_replicas_on_real_redis_resume_a_run_once(stack: Stack) -> None:
    entered, gate = asyncio.Event(), asyncio.Event()

    async def hold(service: str, call: str) -> None:
        if call == "POST /score-all" and not entered.is_set():
            entered.set()
            await gate.wait()

    stack.world.before = hold
    dead = await stack.replica("dead")
    run_id = await dead.start(BODY)
    await asyncio.wait_for(entered.wait(), 5)
    await stack.crash(dead)
    stack.world.before = None
    scored = stack.world.count("validators", "POST /score-all")

    replicas = [await stack.replica(f"r{n}") for n in range(5)]
    results = await asyncio.gather(*(r.recover() for r in replicas))
    assert sum(r.get("resumed", 0) for r in results) == 1
    assert sum(r.get("leased_elsewhere", 0) for r in results) == 4
    owner = next(r for r in replicas if run_id in r._runs)
    await owner.wait(run_id)
    assert stack.world.count("validators", "POST /score-all") == scored + 1


async def test_fifty_paused_runs_recover_from_postgres(stack: Stack) -> None:
    before = await stack.replica("a")
    ids = [await paused(before, idea_id=f"idea_{n}") for n in range(50)]
    await stack.crash(before)
    after = await stack.replica("b", resume_concurrency=4)
    assert await after.recover() == {"paused": 50}
    for run_id in ids:
        question = (await after.get(run_id)).pending_question
        assert question is not None
        await after.answer(run_id, question["question_id"], 50)
    await asyncio.gather(*(after.wait(i) for i in ids))
    assert {(await after.get(i)).state for i in ids} == {"succeeded"}


async def test_the_budget_ledger_survives_in_redis_and_is_restored_when_lost(stack: Stack) -> None:
    before = await stack.replica("a")
    run_id = await paused(before)
    spent = (await before.get(run_id)).spent
    await stack.crash(before)
    await stack.redis.delete(f"budget:{run_id}")  # Redis restarted without persistence
    after = await stack.replica("b")
    await after.recover()
    assert (await after.get(run_id)).spent == spent


async def test_redis_events_are_stored_bounded_and_expire(stack: Stack) -> None:
    publisher = RedisPublisher(stack.redis)
    emitter = RunEmitter("run_ev", publisher)
    for _ in range(3):
        await emitter.emit("heartbeat")
    assert [e.seq for e in await publisher.read("run_ev", 1)] == [2, 3]
    assert await publisher.last_seq("run_ev") == 3
    assert 0 < await stack.redis.ttl("run:run_ev:events") <= 7 * 24 * 3600
    raw = await stack.redis.lrange("run:run_ev:events", 0, -1)
    assert json.loads(raw[0])["seq"] == 1


async def test_the_lifespan_wires_leases_events_and_survives_an_unreachable_m2(
    postgres_url: str, redis_url: str
) -> None:
    settings = Settings(
        internal_token=SecretStr("secret"),
        database_url=SecretStr(postgres_url),
        redis_url=SecretStr(redis_url),
        profile_url="http://127.0.0.1:9",  # nothing listens: recovery fails, startup must not
        recovery_attempts=1,
        recovery_interval_s=0,
        config_dir=CONFIG_DIR,
    )
    app = build_app(settings)
    async with app.router.lifespan_context(app):
        runs: RunService = app.state.runs
        assert runs._leases is not None and runs._events is not None and runs._source is not None
        await asyncio.sleep(0.2)  # the recovery task has failed once and ended
    # shutting down cancelled nothing it should not have: no exception escaped


async def test_the_sequence_survives_losing_the_event_list_on_real_redis(stack: Stack) -> None:
    publisher = RedisPublisher(stack.redis)
    emitter = RunEmitter("run_seq", publisher)
    for _ in range(5):
        await emitter.emit("heartbeat")
    await stack.redis.delete("run:run_seq:events")
    assert await publisher.last_seq("run_seq") == 5
    again = RunEmitter("run_seq", publisher)
    again.resume_from(await publisher.last_seq("run_seq"))
    assert (await again.emit("heartbeat")).seq == 6
    assert [e.seq for e in await publisher.read("run_seq", 4)] == [6]


async def test_a_poison_run_is_given_up_on_real_redis(stack: Stack) -> None:
    before = await stack.replica("a")
    run_id = await paused(before)
    await stack.crash(before)
    for n in range(3):  # three safe restores each reset the count
        replica = await stack.replica(f"ok{n}", recovery_attempt_limit=1)
        assert await replica.recover() == {"paused": 1}
        await stack.crash(replica)
    await stack.redis.set(f"run:{run_id}:recoveries", 5)  # as if it had crashed five times
    last = await stack.replica("last")
    assert await last.recover() == {"failed": 1}
