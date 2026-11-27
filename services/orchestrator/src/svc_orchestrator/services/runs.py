"""Run lifecycle (docs/harness/07 §1): start, run in the background, pause, resume, finish.

A run is one LangGraph thread (``thread_id = run_id``). The graph runs in a background task; when it
hits ``interrupt()`` the run becomes ``awaiting_input`` / ``awaiting_approval`` and the task ends.
``answer`` / ``approve`` resume the same thread. Unanswered questions and approvals expire (a
question falls back to the scorer's estimate, an approval counts as declined).

Run state is held in this process and mirrored to M2 (``PUT /internal/runs/{id}``) after every
transition. Business logic here has no web-framework imports.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from common.errors import Conflict, NotFound, ValidationFailed
from common.logging import get_logger

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


@dataclass
class ActiveRun:
    run_id: str
    user_id: str
    ctx: RunContext
    graph: CompiledStateGraph[Any, Any, Any, Any]
    state_model: type[BaseModel]
    config: RunnableConfig
    kind: str = KIND
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
    ) -> None:
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
        )
        self._runs[run_id] = run
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
        if isinstance(spec, DiscoverInput):
            return (
                build_discover_graph(ctx, self._checkpointer),
                DiscoverState(
                    run_id=run_id, user_id=body.user_id, niche=spec.niche, count=spec.count
                ),
            )
        if isinstance(spec, ChallengeInput):
            return (
                build_challenge_graph(ctx, self._checkpointer),
                ChallengeState(
                    run_id=run_id,
                    user_id=body.user_id,
                    idea_id=spec.idea_id,
                    version=spec.version,
                    claim_id=spec.claim_id,
                ),
            )
        graph = build_validate_graph(ctx, self._checkpointer)
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
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- internals ---------------------------------------------------------------------------
    def _get(self, run_id: str) -> ActiveRun:
        run = self._runs.get(run_id)
        if run is None:
            raise NotFound(f"no run {run_id}")
        return run

    def _launch(self, run: ActiveRun, graph_input: BaseModel | Command[Any]) -> None:
        run.status = RunStatus.RUNNING
        run.pending_question = run.pending_approval = None
        if run.expiry is not None:
            run.expiry.cancel()
            run.expiry = None
        run.task = asyncio.create_task(self._drive(run, graph_input))

    def _resume(self, run: ActiveRun, value: Any) -> None:
        self._launch(run, Command(resume=value))

    async def _drive(self, run: ActiveRun, graph_input: BaseModel | Command[Any]) -> None:
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
        spent = await run.ctx.ledger.spent()
        record = {
            "run_id": run.run_id,
            "kind": run.kind,
            "user_id": run.user_id,
            "state": run.status.value,
            "step": run.step,
            "spent": spent.model_dump(),
            "pending_question": run.pending_question,
            "pending_approval": run.pending_approval,
            "result": run.result,
            "error": run.error,
        }
        try:
            await run.ctx.clients.profile.put_run(run.run_id, record)
        except Exception as exc:  # the live run is the source of truth; M2 is a mirror
            log.warning("run_persist_failed", run_id=run.run_id, error=type(exc).__name__)
