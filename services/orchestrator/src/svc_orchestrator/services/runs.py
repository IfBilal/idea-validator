"""Run lifecycle (docs/harness/07 §1): start, run in the background, pause, resume, finish.

A run is one LangGraph thread (``thread_id = run_id``). The graph runs in a background task; when it
hits ``interrupt()`` the run becomes ``awaiting_input`` / ``awaiting_approval`` and the task ends.
``answer`` / ``approve`` resume the same thread. Unanswered questions and approvals expire (a
question falls back to the scorer's estimate, an approval counts as declined).

Run state is held in this process and mirrored to M2 (``PUT /internal/runs/{id}``) after every
transition. After a restart ``recover`` lists the runs M2 still calls unfinished and rehydrates each
from its LangGraph checkpoint (docs/harness/07 §1): a paused run gets its question or approval back,
a running one continues from its last checkpoint, one with no readable checkpoint is failed with a
fixed reason. A lease per run keeps two replicas from driving the same run. Business logic here has
no web-framework imports.
"""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from common.budget import Spend
from common.errors import Conflict, NotFound, ValidationFailed
from common.logging import get_logger
from common.schemas import RunEvent

from ..clients.profile import StoredRun
from ..models.api import (
    ChallengeInput,
    DiscoverInput,
    RunView,
    StartRun,
    ValidateInput,
    WhatIfInput,
    check_in_range,
)
from ..models.challenge_state import ChallengeState
from ..models.discover_state import DiscoverState
from ..models.state import TERMINAL, RunStatus, ValidateState
from ..repositories.events import EventLog
from ..repositories.leases import RunLeases
from .challenge import build_challenge_graph
from .discover_graph import build_discover_graph
from .run_config import RunConfig
from .run_context import RunContext
from .validate_graph import build_validate_graph, graph_config

log = get_logger("runs")

NO_ANSWER = "__no_answer__"  # resume value for an expired question (anything non-numeric works)
DECLINE = "decline"
KIND = "validate"
MAX_REMEMBERED_IDEAS = 100
RECOVERY_FAILED = "the run could not be recovered after a restart"  # fixed: never an exception text
RECOVERABLE_STATES = ("created", "running", "awaiting_input", "awaiting_approval")
STATE_MODELS: dict[str, type[BaseModel]] = {
    "validate": ValidateState,
    "whatif": ValidateState,
    "discover": DiscoverState,
    "challenge": ChallengeState,
}
RESTORE_PARALLELISM = 16  # checkpoint reads at once; resuming graphs is bounded separately


class RunSource(Protocol):
    """Where unfinished runs are listed from after a restart (M2's run mirror)."""

    async def list_runs(
        self, states: Sequence[str], *, limit: int = 100, after: str = ""
    ) -> list[StoredRun]: ...


@dataclass
class ActiveRun:
    run_id: str
    user_id: str
    ctx: RunContext
    graph: CompiledStateGraph[Any, Any, Any, Any]
    state_model: type[BaseModel]
    config: RunnableConfig
    kind: str = KIND
    input: dict[str, Any] = field(default_factory=dict)  # the start request, kept for the M2 mirror
    leased: bool = False
    status: RunStatus = RunStatus.CREATED
    step: str = "INTAKE"
    pending_question: dict[str, Any] | None = None
    pending_approval: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None
    expiry: asyncio.Task[None] | None = None
    background: set[asyncio.Task[None]] = field(default_factory=set)


class RunService:
    def __init__(
        self,
        *,
        context_factory: Callable[[str, str], RunContext],
        checkpointer: BaseCheckpointSaver[Any] | None,
        config: RunConfig,
        question_timeout_s: float = 600,
        leases: RunLeases | None = None,
        event_log: EventLog | None = None,
        run_source: RunSource | None = None,
        resume_concurrency: int = 4,
        heartbeat_s: float = 20.0,
        recovery_page_size: int = 100,
    ) -> None:
        if resume_concurrency < 1 or recovery_page_size < 1 or heartbeat_s <= 0:
            raise ValueError("resume_concurrency, recovery_page_size and heartbeat_s must be > 0")
        self._leases = leases
        self._events = event_log
        self._source = run_source
        self._resume_slots = asyncio.Semaphore(resume_concurrency)
        self._restore_slots = asyncio.Semaphore(RESTORE_PARALLELISM)
        self._heartbeat_s = heartbeat_s
        self._page_size = recovery_page_size
        self._heartbeat: asyncio.Task[None] | None = None
        self._restoring: set[str] = set()
        self._factory = context_factory
        self._checkpointer = checkpointer
        self._config = config
        self._question_timeout_s = question_timeout_s
        self._runs: dict[str, ActiveRun] = {}
        self._memos: dict[str, dict[str, Any]] = {}  # idea id -> memo of its latest validate run

    # ---- commands ----------------------------------------------------------------------------
    async def start(self, body: StartRun) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        ctx = self._factory(run_id, body.kind)
        graph, state = self._prepare(body, run_id, ctx)
        run = ActiveRun(
            run_id=run_id,
            user_id=body.user_id,
            ctx=ctx,
            graph=graph,
            state_model=type(state),
            config=graph_config(run_id),
            kind=body.kind,
            input=body.input.model_dump(mode="json"),
        )
        self._runs[run_id] = run
        await self._lease(run)
        limits = await ctx.ledger.limits()
        await ctx.emitter.emit("run_started", kind=body.kind, budget=limits.model_dump())
        self._launch(run, state)
        await self._persist(run)
        return run_id

    def _prepare(
        self, body: StartRun, run_id: str, ctx: RunContext
    ) -> tuple[CompiledStateGraph[Any, Any, Any, Any], BaseModel]:
        """The graph and the first state for this kind of run."""
        spec = body.input
        graph = self._graph_for(body.kind, ctx)
        if isinstance(spec, DiscoverInput):
            return (
                graph,
                DiscoverState(
                    run_id=run_id, user_id=body.user_id, niche=spec.niche, count=spec.count
                ),
            )
        if isinstance(spec, ChallengeInput):
            return (
                graph,
                ChallengeState(
                    run_id=run_id,
                    user_id=body.user_id,
                    idea_id=spec.idea_id,
                    version=spec.version,
                    claim_id=spec.claim_id,
                ),
            )
        if isinstance(spec, WhatIfInput):
            return graph, ValidateState(
                run_id=run_id,
                user_id=body.user_id,
                idea_id=spec.idea_id,
                idea_version=spec.version + 1,  # M2 assigns the real number when it saves
                idea_text="",  # the rewrite step fills it in
                whatif={
                    "base_version": spec.version,
                    "change_id": spec.change_id,
                    "custom_change": spec.custom_change,
                },
                memo=dict(self._memos.get(spec.idea_id, {})),
            )
        if isinstance(spec, ValidateInput):
            return graph, ValidateState(
                run_id=run_id,
                user_id=body.user_id,
                idea_id=spec.idea_id,
                idea_version=spec.version,
                idea_text=spec.idea_text,
                title=spec.title,
                overrides=spec.overrides,
                unknowns=[u.model_dump(mode="json") for u in spec.unknowns],
            )
        raise ValidationFailed(f"unsupported run kind {body.kind!r}")  # unreachable: validated

    def _graph_for(self, kind: str, ctx: RunContext) -> CompiledStateGraph[Any, Any, Any, Any]:
        if kind == "discover":
            return build_discover_graph(ctx, self._checkpointer)
        if kind == "challenge":
            return build_challenge_graph(ctx, self._checkpointer)
        return build_validate_graph(ctx, self._checkpointer)  # validate and whatif

    async def answer(self, run_id: str, question_id: str, answer: float) -> None:
        run = self._get(run_id)
        question = run.pending_question
        if run.status is not RunStatus.AWAITING_INPUT or question is None:
            raise Conflict("the run is not waiting for an answer", {"state": run.status.value})
        if question["question_id"] != question_id:
            raise Conflict("that is not the open question", {"expected": question["question_id"]})
        self._check_answer(question, answer)
        self._resume(run, answer)
        await self._persist(run)

    @staticmethod
    def _check_answer(question: dict[str, Any], answer: float) -> None:
        """Reject out-of-range answers before they can reach M11; the run keeps waiting."""
        try:
            check_in_range(str(question["field"]), answer)
            low, high = question["range"]
            if not low <= answer <= high:
                raise ValueError(f"the answer must be between {low:g} and {high:g}")
        except ValueError as exc:
            raise ValidationFailed(str(exc), {"question_id": question["question_id"]}) from exc

    async def approve(self, run_id: str, approval_id: str, decision: str) -> None:
        run = self._get(run_id)
        approval = run.pending_approval
        if run.status is not RunStatus.AWAITING_APPROVAL or approval is None:
            raise Conflict("the run is not waiting for an approval", {"state": run.status.value})
        if approval["approval_id"] != approval_id:
            raise Conflict("that is not the open approval", {"expected": approval["approval_id"]})
        self._resume(run, "approve" if decision == "approve" else DECLINE)
        await self._persist(run)

    # ---- queries -----------------------------------------------------------------------------
    async def get(self, run_id: str) -> RunView:
        run = self._get(run_id)
        spent = await run.ctx.ledger.spent()
        limits = await run.ctx.ledger.limits()
        return RunView(
            run_id=run_id,
            kind=run.kind,
            state=run.status.value,
            progress={"step": run.step},
            spent=spent.model_dump(),
            budget=limits.model_dump(),
            pending_question=run.pending_question,
            pending_approval=run.pending_approval,
            result=run.result,
            error=run.error,
        )

    async def events(self, run_id: str, after: int = 0) -> list[RunEvent]:
        """Stored events of a run with ``seq`` > ``after`` (empty when events are not stored)."""
        return [] if self._events is None else await self._events.read(run_id, after)

    async def wait(self, run_id: str) -> None:
        """Wait until the run finishes or pauses (tests and graceful shutdown)."""
        run = self._get(run_id)
        while run.task is not None and not run.task.done():
            await asyncio.shield(run.task)

    async def aclose(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        for run in self._runs.values():
            for task in (run.task, run.expiry):
                if task is not None and not task.done():
                    task.cancel()
                    tasks.append(task)
        if self._heartbeat is not None and not self._heartbeat.done():
            self._heartbeat.cancel()
            tasks.append(self._heartbeat)
        await asyncio.gather(*tasks, return_exceptions=True)
        for run in list(self._runs.values()):
            await self._release(run)  # hand the runs over at once instead of after the lease TTL

    # ---- recovery ----------------------------------------------------------------------------
    async def recover(self) -> dict[str, int]:
        """Rehydrate the runs M2 lists as unfinished; returns how many ended in each outcome.

        Outcomes: ``paused`` (question or approval is open again), ``resumed`` (continues from its
        checkpoint), ``finished`` (it had completed; the mirror is corrected), ``failed`` (no usable
        checkpoint), ``known`` (already driven here), ``leased_elsewhere`` (another replica has it).
        Raises when M2 cannot be listed; the caller retries (``recovery_loop``).
        """
        outcomes: dict[str, int] = {}
        if self._source is None:
            return outcomes
        after = ""
        while True:
            page = await self._source.list_runs(
                RECOVERABLE_STATES, limit=self._page_size, after=after
            )
            for outcome in await asyncio.gather(*(self._restore_bounded(r) for r in page)):
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if len(page) < self._page_size:
                return outcomes
            after = page[-1].run_id

    async def _restore_bounded(self, stored: StoredRun) -> str:
        async with self._restore_slots:
            return await self.restore(stored)

    async def restore(self, stored: StoredRun) -> str:
        run_id = stored.run_id
        if run_id in self._runs or run_id in self._restoring:
            return "known"
        self._restoring.add(run_id)  # single flight inside this process
        try:
            if self._leases is not None and not await self._leases.acquire(run_id):
                return "leased_elsewhere"  # single flight across replicas
            try:
                return await self._restore_owned(stored)
            except Exception as exc:
                log.error("run_recovery_error", run_id=run_id, error=type(exc).__name__)
                self._runs.pop(run_id, None)
                if self._leases is not None:
                    await self._leases.release(run_id)
                return "error"
        finally:
            self._restoring.discard(run_id)

    async def _restore_owned(self, stored: StoredRun) -> str:
        kind = stored.kind
        ctx = self._factory(stored.run_id, kind if kind in STATE_MODELS else KIND)
        if self._events is not None:
            ctx.emitter.resume_from(await self._events.last_seq(stored.run_id))
        if kind not in STATE_MODELS:
            return await self._give_up(None, stored, ctx)
        await self._restore_ledger(ctx, stored)
        model = STATE_MODELS[kind]
        run = ActiveRun(
            run_id=stored.run_id,
            user_id=stored.user_id,
            ctx=ctx,
            graph=self._graph_for(kind, ctx),
            state_model=model,
            config=graph_config(stored.run_id),
            kind=kind,
            input=stored.input,
            leased=self._leases is not None,
            step=str(stored.context.get("step", "INTAKE")),
        )
        self._runs[run.run_id] = run
        self._ensure_heartbeat()
        try:
            snapshot = await run.graph.aget_state(run.config)
            state = model.model_validate(snapshot.values)
        except Exception as exc:  # no checkpoint, or one that cannot be read
            log.warning("run_checkpoint_unusable", run_id=run.run_id, error=type(exc).__name__)
            return await self._give_up(run, stored, ctx)
        if getattr(state, "run_id", None) != run.run_id:  # a checkpoint that is not this run's
            log.warning("run_checkpoint_mismatch", run_id=run.run_id)
            return await self._give_up(run, stored, ctx)
        run.step = getattr(state, "step", run.step)
        for task in snapshot.tasks:
            for pending in task.interrupts:
                self._pause(run, dict(pending.value))
                await self._persist(run)
                return "paused"
        if snapshot.next:
            run.status = RunStatus.RUNNING
            await ctx.emitter.emit("heartbeat", recovered=True, step=run.step)
            run.task = asyncio.create_task(self._drive_recovered(run))
            return "resumed"
        await self._settle(run)
        await self._persist(run)
        return "finished"

    async def _restore_ledger(self, ctx: RunContext, stored: StoredRun) -> None:
        """A Redis that lost the budget hash gets the spend M3 last mirrored to M2."""
        try:
            spent = Spend.model_validate(stored.context.get("spent") or {})
        except ValueError:
            return
        await ctx.ledger.restore_if_empty(spent)

    async def _give_up(self, run: ActiveRun | None, stored: StoredRun, ctx: RunContext) -> str:
        """No usable checkpoint: fail the run with a fixed reason, in memory and in M2."""
        await ctx.emitter.emit("run_failed", status="failed", reason=RECOVERY_FAILED)
        if run is not None:
            run.status, run.error = RunStatus.FAILED, RECOVERY_FAILED
            run.pending_question = run.pending_approval = None
            await self._persist(run)
        else:
            context = {"step": stored.context.get("step"), "error": RECOVERY_FAILED}
            await self._mirror(
                ctx,
                stored.run_id,
                stored.user_id,
                stored.kind,
                "failed",
                stored.input,
                context,
                None,
            )
            if self._leases is not None:
                await self._leases.release(stored.run_id)
        return "failed"

    async def _drive_recovered(self, run: ActiveRun) -> None:
        async with self._resume_slots:  # a restart must not start every run's work at once
            await self._drive(run, None)

    # ---- leases ------------------------------------------------------------------------------
    async def _lease(self, run: ActiveRun) -> None:
        if self._leases is None:
            return
        if not await self._leases.acquire(run.run_id):
            raise Conflict("another replica owns this run", {"run_id": run.run_id})
        run.leased = True
        self._ensure_heartbeat()

    async def _release(self, run: ActiveRun) -> None:
        if self._leases is None or not run.leased:
            return
        run.leased = False
        try:
            await self._leases.release(run.run_id)
        except Exception as exc:  # the lease expires on its own
            log.warning("lease_release_failed", run_id=run.run_id, error=type(exc).__name__)

    def _ensure_heartbeat(self) -> None:
        if self._leases is not None and (self._heartbeat is None or self._heartbeat.done()):
            self._heartbeat = asyncio.create_task(self._renew_leases())

    async def _renew_leases(self) -> None:
        """Keep the leases of the runs this replica drives; stop driving one that was taken over."""
        assert self._leases is not None
        while True:
            await asyncio.sleep(self._heartbeat_s)
            for run in [r for r in self._runs.values() if r.leased]:
                try:
                    held = await self._leases.renew(run.run_id)
                except Exception as exc:  # Redis hiccup: the TTL spans several beats
                    log.warning("lease_renew_failed", run_id=run.run_id, error=type(exc).__name__)
                    continue
                if not held:
                    await self._abandon(run)

    async def _abandon(self, run: ActiveRun) -> None:
        log.error("lease_lost", run_id=run.run_id)
        run.leased = False
        tasks = [t for t in (run.task, run.expiry) if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.pop(run.run_id, None)

    # ---- internals ---------------------------------------------------------------------------
    def _get(self, run_id: str) -> ActiveRun:
        run = self._runs.get(run_id)
        if run is None:
            raise NotFound(f"no run {run_id}")
        return run

    def _launch(self, run: ActiveRun, graph_input: BaseModel | Command[Any] | None) -> None:
        run.status = RunStatus.RUNNING
        run.pending_question = run.pending_approval = None
        if run.expiry is not None:
            run.expiry.cancel()
            run.expiry = None
        run.task = asyncio.create_task(self._drive(run, graph_input))

    def _resume(self, run: ActiveRun, value: Any) -> None:
        self._launch(run, Command(resume=value))

    async def _drive(self, run: ActiveRun, graph_input: BaseModel | Command[Any] | None) -> None:
        try:
            await run.graph.ainvoke(graph_input, run.config)
            await self._settle(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leak internals to the caller
            log.error("run_crashed", run_id=run.run_id, error=type(exc).__name__, exc_info=True)
            run.status, run.error = RunStatus.FAILED, "internal error"
            await run.ctx.emitter.emit("run_failed", status="failed", reason=run.error)
        await self._persist(run)

    async def _settle(self, run: ActiveRun) -> None:
        snapshot = await run.graph.aget_state(run.config)
        for task in snapshot.tasks:
            for pending in task.interrupts:
                self._pause(run, dict(pending.value))
                return
        state = run.state_model.model_validate(snapshot.values)
        run.step = getattr(state, "step", run.step)
        status = getattr(state, "status", RunStatus.SUCCEEDED)
        run.status = status if status in TERMINAL else RunStatus.SUCCEEDED
        run.result, run.error = getattr(state, "card", None), getattr(state, "error", None)
        if isinstance(state, ValidateState) and state.memo:
            self._remember(state)

    def _pause(self, run: ActiveRun, payload: dict[str, Any]) -> None:
        kind = payload.pop("type")
        if kind == "question":
            run.status, run.step, run.pending_question = (
                RunStatus.AWAITING_INPUT,
                "WAIT_ANSWER",
                payload,
            )
            timeout, value = self._question_timeout_s, NO_ANSWER
        else:
            run.status, run.step, run.pending_approval = (
                RunStatus.AWAITING_APPROVAL,
                "WAIT_APPROVAL",
                payload,
            )
            timeout, value = self._config.approval_timeout_s, DECLINE
        run.expiry = asyncio.create_task(self._expire(run, payload, timeout, value))

    async def _expire(
        self, run: ActiveRun, payload: dict[str, Any], timeout_s: float, value: str
    ) -> None:
        await asyncio.sleep(timeout_s)
        still_open = payload in (run.pending_question, run.pending_approval)
        if still_open:
            log.info("pending_input_expired", run_id=run.run_id)
            run.expiry = None  # this task is about to launch the resumed one
            self._resume(run, value)

    def _remember(self, state: ValidateState) -> None:
        """Keep an idea's latest step outputs so a what-if re-runs only what changed."""
        self._memos.pop(state.idea_id, None)
        self._memos[state.idea_id] = state.memo
        while len(self._memos) > MAX_REMEMBERED_IDEAS:
            self._memos.pop(next(iter(self._memos)))

    async def _persist(self, run: ActiveRun) -> None:
        """Mirror the run to M2 in M2's shape: ``context`` carries what M2 has no column for."""
        spent = await run.ctx.ledger.spent()
        context = {
            "step": run.step,
            "spent": spent.model_dump(),
            "pending_approval": run.pending_approval,
            "result": run.result,
            "error": run.error,
        }
        await self._mirror(
            run.ctx,
            run.run_id,
            run.user_id,
            run.kind,
            run.status.value,
            run.input,
            context,
            run.pending_question,
        )
        if run.status in TERMINAL:
            await self._release(run)

    @staticmethod
    async def _mirror(
        ctx: RunContext,
        run_id: str,
        user_id: str,
        kind: str,
        state: str,
        run_input: dict[str, Any],
        context: dict[str, Any],
        pending_question: dict[str, Any] | None,
    ) -> None:
        record = {
            "user_id": user_id,
            "kind": kind,
            "state": state,
            "input": run_input,
            "context": context,
            "pending_question": pending_question,
        }
        try:
            await ctx.clients.profile.put_run(run_id, record)
        except Exception as exc:  # the live run is the source of truth; M2 is a mirror
            log.warning("run_persist_failed", run_id=run_id, error=type(exc).__name__)
