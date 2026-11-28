"""Starting, reading and following runs. M2 decides who owns what; M3 does the work.

The gateway never computes, edits or rounds a verdict: ``result`` and ``card`` are passed through
exactly as M3 and M2 returned them (CLAUDE.md rule 1).

Starting a run touches three systems (M2 idea, M3 run, M2 owner record) that cannot share a
transaction, so the flow is built to be safely repeatable:
- every start is keyed (``Idempotency-Key`` header, or a hash of user, kind and body), so a retry or
  a double submit gets the run it already started;
- a new idea or version is remembered under that key, so a failed start never piles up drafts;
- if the owner record cannot be written, the run M3 just started is cancelled again.
"""

import hashlib
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from common.errors import (
    Conflict,
    DomainError,
    NotFound,
    RateLimited,
    Unavailable,
    UpstreamError,
    ValidationFailed,
)
from common.logging import get_logger

from ..clients.agentops import AgentOpsClient
from ..clients.coordination import PENDING_TTL_S, IdempotencyStore, StreamGate
from ..clients.events import EventBus
from ..clients.ideas import IdeaClient
from ..clients.orchestrator import OrchestratorClient
from ..clients.upstream import UNAVAILABLE
from ..models.ideas import RunRecord
from ..models.runs import (
    TERMINAL_STATES,
    AnswerBody,
    ApproveBody,
    ChallengeBody,
    DiscoverBody,
    RunAccepted,
    RunView,
    StepAccepted,
    ValidateAccepted,
    ValidateBody,
    WhatIfBody,
)
from .streaming import DEFAULT_MAX_FRAME, parse_last_event_id, stream_run_events

QUESTION_ID = re.compile(r"q_[A-Za-z0-9_]{1,64}")
log = get_logger("gateway.runs")

OWNER_WRITE_ATTEMPTS = 3
RETRYABLE_STATES = frozenset({"failed", "budget_stopped"})  # a repeat of these starts a new run


@dataclass(frozen=True)
class RunFlowConfig:
    heartbeat_s: float = 15.0
    stream_max_s: float = 900.0
    max_frame_bytes: int = DEFAULT_MAX_FRAME
    streams_per_user: int = 3
    streams_global: int = 100
    header_key_ttl_s: int = 600
    derived_key_ttl_s: int = 60


class GuardedStream:
    """The frame generator plus the release of the stream slots, exactly once.

    A plain async generator does not run its ``finally`` if it was never started, so the slots are
    released here: on normal end, on error, on cancellation and on ``aclose``.
    """

    def __init__(self, inner: AsyncGenerator[str], release: Callable[[], Awaitable[None]]) -> None:
        self._inner = inner
        self._release = release
        self._done = False

    def __aiter__(self) -> "GuardedStream":
        return self

    async def __anext__(self) -> str:
        try:
            return await self._inner.__anext__()
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            await self._inner.aclose()
        finally:
            await self._release()


def request_key(user_id: str, kind: str, body: dict[str, Any], header: str | None) -> str:
    raw = json.dumps({"u": user_id, "k": kind, "b": body, "h": header or ""}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


class RunFlow:
    def __init__(
        self,
        ideas: IdeaClient,
        orchestrator: OrchestratorClient,
        bus: EventBus,
        store: IdempotencyStore,
        gate: StreamGate,
        config: RunFlowConfig | None = None,
        agentops: AgentOpsClient | None = None,
    ) -> None:
        self._agentops = agentops
        self._ideas = ideas
        self._orchestrator = orchestrator
        self._bus = bus
        self._store = store
        self._gate = gate
        self._config = config or RunFlowConfig()
        self._open_streams = 0

    # ---- starting runs -----------------------------------------------------------------------
    async def validate(
        self, user_id: str, body: ValidateBody, idem: str | None = None
    ) -> ValidateAccepted:
        fingerprint = {
            "text": body.idea_text,
            "title": body.title,
            "idea_id": str(body.idea_id) if body.idea_id else None,
        }
        key = request_key(user_id, "validate", fingerprint, idem)
        ttl = self._ttl(idem)
        saved = await self._begin(user_id, key, ttl)
        if "run_id" in saved:
            return ValidateAccepted(
                run_id=saved["run_id"], idea_id=saved["idea_id"], version=saved["version"]
            )
        try:
            if "idea_id" not in saved:
                saved = await self._prepare_idea(user_id, body)
                await self._store.put(key, saved, ttl)  # a failed start must not add another draft
        except BaseException:
            await self._forget(key)
            raise
        run_input: dict[str, Any] = {
            "idea_text": body.idea_text,
            "idea_id": saved["idea_id"],
            "version": saved["version"],
        }
        if body.idea_id is None:
            run_input["title"] = body.title  # an existing idea keeps the title it has
        run_id = await self._start(user_id, "validate", run_input)
        await self._store.put(key, {**saved, "run_id": run_id}, ttl)
        return ValidateAccepted(run_id=run_id, idea_id=saved["idea_id"], version=saved["version"])

    async def _prepare_idea(self, user_id: str, body: ValidateBody) -> dict[str, Any]:
        if body.idea_id is None:
            assert body.title is not None  # the body model guarantees it
            created = await self._ideas.create_idea(user_id, body.title, body.idea_text)
            return {"idea_id": created.idea_id, "version": created.version}
        idea_id = str(body.idea_id)
        existing = await self._ideas.versions(idea_id, user_id)  # 404 unless it is theirs
        latest = max(existing, key=lambda v: v.version, default=None)
        if latest is not None and latest.text == body.idea_text:
            return {"idea_id": idea_id, "version": latest.version}  # same text: same version
        added = await self._ideas.add_version(idea_id, user_id, body.idea_text)
        return {"idea_id": idea_id, "version": added.version}

    async def discover(
        self, user_id: str, body: DiscoverBody, idem: str | None = None
    ) -> RunAccepted:
        run_input = {"niche": body.niche, "count": body.count, "deep_search": body.deep_search}
        return RunAccepted(run_id=await self._once(user_id, "discover", run_input, idem))

    async def challenge(
        self, user_id: str, idea_id: str, body: ChallengeBody, idem: str | None = None
    ) -> RunAccepted:
        card = (await self._ideas.detail(idea_id, user_id, body.version)).card
        if card is None:
            raise Conflict("this version has no verdict yet")
        claims = card.get("claims")
        known = (
            {c.get("claim_id") for c in claims if isinstance(c, dict)}
            if isinstance(claims, list)
            else set()
        )
        if body.claim_id not in known:
            raise NotFound("claim not found")
        run_input = {"idea_id": idea_id, "version": body.version, "claim_id": body.claim_id}
        return RunAccepted(run_id=await self._once(user_id, "challenge", run_input, idem))

    async def whatif(
        self, user_id: str, idea_id: str, body: WhatIfBody, idem: str | None = None
    ) -> RunAccepted:
        if (await self._ideas.detail(idea_id, user_id, body.version)).card is None:
            raise Conflict("this version has no verdict yet")
        run_input: dict[str, Any] = {"idea_id": idea_id, "version": body.version}
        if body.change_id is not None:
            run_input["change_id"] = body.change_id
        else:
            run_input["custom_change"] = body.custom_change
        return RunAccepted(run_id=await self._once(user_id, "whatif", run_input, idem))

    # ---- idempotency and the start itself ------------------------------------------------------
    def _ttl(self, idem: str | None) -> int:
        return self._config.header_key_ttl_s if idem else self._config.derived_key_ttl_s

    async def _begin(self, user_id: str, key: str, ttl_s: int) -> dict[str, Any]:
        """What an earlier attempt with this key left behind (``{}`` when this is the first)."""
        for _ in range(3):
            saved = await self._store.get(key)
            if saved is None:
                if await self._store.claim(key, PENDING_TTL_S):
                    return {}
                continue
            if saved.get("pending"):
                raise Conflict("the same request is still being processed")
            run_id = saved.get("run_id")
            if not isinstance(run_id, str):
                return saved  # an idea was prepared, the run was not started
            if await self._reusable(user_id, run_id):
                return saved
            idea_only = {k: v for k, v in saved.items() if k != "run_id"}
            await self._store.put(key, idea_only, ttl_s)
            return idea_only
        raise Conflict("the same request is still being processed")

    async def _reusable(self, user_id: str, run_id: str) -> bool:
        try:
            record = await self._ideas.get_run(run_id, user_id)
        except NotFound:
            return False
        return record.state not in RETRYABLE_STATES

    async def _forget(self, key: str) -> None:
        with suppress(DomainError):
            await self._store.delete(key)

    async def _once(
        self, user_id: str, kind: str, run_input: dict[str, Any], idem: str | None
    ) -> str:
        key = request_key(user_id, kind, run_input, idem)
        ttl = self._ttl(idem)
        saved = await self._begin(user_id, key, ttl)
        if isinstance(saved.get("run_id"), str):
            return str(saved["run_id"])
        try:
            run_id = await self._start(user_id, kind, run_input)
        except BaseException:
            await self._forget(key)
            raise
        await self._store.put(key, {"run_id": run_id}, ttl)
        return run_id

    async def _start(self, user_id: str, kind: str, run_input: dict[str, Any]) -> str:
        run_id = await self._orchestrator.start(kind, user_id, run_input)
        last: DomainError | None = None
        for _ in range(OWNER_WRITE_ATTEMPTS):
            try:  # M3 chose the id, so the owner can only be recorded now
                await self._ideas.put_run(run_id, user_id, kind, "running", run_input)
            except DomainError as exc:
                last = exc
                continue
            log.info("run_started", run_id=run_id, kind=kind)
            return run_id
        # a run nobody owns could never be read or stopped by its user: stop it now
        await self._orchestrator.cancel(run_id, user_id)
        log.error("owner_record_failed", run_id=run_id, kind=kind)
        raise last if last is not None else UpstreamError(UNAVAILABLE)

    # ---- reading and steering a run ----------------------------------------------------------
    async def get(self, user_id: str, run_id: str) -> RunView:
        record = await self._ideas.get_run(run_id, user_id)
        live = await self._orchestrator.get(run_id, user_id)
        if live is not None:
            if live.state in TERMINAL_STATES and record.state != live.state:
                await self._sync(user_id, record, live.state)
            return live
        state = await self._settled_state(user_id, record)
        return RunView(  # M3 forgot the run (restart): the stored record is all there is
            run_id=run_id,
            kind=record.kind,
            state=state,
            progress={},
            spent={},
            budget={},
            pending_question=None,
            pending_approval=None,
            result=None,
        )

    async def _settled_state(self, user_id: str, record: RunRecord) -> str:
        """M3 no longer knows the run: one M2 still calls running is over, whatever it says."""
        if record.state in TERMINAL_STATES:
            return record.state
        await self._sync(user_id, record, "failed")
        return "failed"

    async def _sync(self, user_id: str, record: RunRecord, state: str) -> None:
        try:
            await self._ideas.put_run(record.run_id, user_id, record.kind, state, record.input)
        except DomainError as exc:  # a stale mirror is a nuisance, not a reason to fail a read
            log.warning("run_state_sync_failed", run_id=record.run_id, error=type(exc).__name__)

    async def answer(self, user_id: str, run_id: str, body: AnswerBody) -> StepAccepted:
        await self._ideas.get_run(run_id, user_id)  # ownership first: nothing else is revealed
        if not QUESTION_ID.fullmatch(body.question_id):
            raise ValidationFailed("question_id is not a valid question id")
        state = await self._orchestrator.answer(run_id, user_id, body.question_id, body.answer)
        return StepAccepted(run_id=run_id, state=state)

    async def approve(self, user_id: str, run_id: str, body: ApproveBody) -> StepAccepted:
        await self._ideas.get_run(run_id, user_id)
        if not re.fullmatch(rf"ap_{re.escape(run_id)}_[a-z0-9_]{{1,40}}", body.approval_id):
            raise ValidationFailed("approval_id does not belong to this run")
        state = await self._orchestrator.approve(run_id, user_id, body.approval_id, body.decision)
        return StepAccepted(run_id=run_id, state=state)

    async def trace(self, user_id: str, run_id: str, flat: bool) -> dict[str, Any]:
        """The recorded trace of one of the caller's own runs (M13), never anybody else's."""
        await self._ideas.get_run(run_id, user_id)  # ownership first: unknown and foreign are 404
        if self._agentops is None:
            raise Unavailable("run traces are not available")
        return await self._agentops.trace(run_id, flat)

    # ---- the event stream --------------------------------------------------------------------
    async def open_events(
        self, user_id: str, run_id: str, last_event_id: str | None, max_s: float
    ) -> GuardedStream:
        """Check ownership and take a stream slot now (so refusals are normal JSON errors)."""
        record = await self._ideas.get_run(run_id, user_id)
        config = self._config
        if self._open_streams >= config.streams_global:
            raise Unavailable("too many open streams, try again shortly")
        slot_ttl = int(config.stream_max_s) + 60  # a crashed replica's slots expire on their own
        if not await self._gate.acquire(user_id, config.streams_per_user, slot_ttl):
            raise RateLimited(
                "too many open streams, close one first",
                {"retry_after": 5, "limit": config.streams_per_user},
            )
        self._open_streams += 1

        async def release() -> None:
            self._open_streams -= 1
            try:
                await self._gate.release(user_id)
            except DomainError as exc:
                log.warning("stream_slot_release_failed", error=type(exc).__name__)

        try:  # fail closed: a stream that cannot work answers 503 now, not a silent 200 later
            await self._orchestrator.get(run_id, user_id)
            subscription = await self._bus.subscribe(run_id)
        except Exception as exc:
            await release()
            log.warning("stream_preflight_failed", run_id=run_id, error=type(exc).__name__)
            raise Unavailable("live events are temporarily unavailable") from exc

        async def replay(after: int) -> list[Any]:
            return await self._orchestrator.events_after(run_id, user_id, after)

        async def state() -> str | None:
            live = await self._orchestrator.get(run_id, user_id)
            if live is None:
                return await self._settled_state(user_id, record)
            if live.state in TERMINAL_STATES and record.state != live.state:
                await self._sync(user_id, record, live.state)
            return live.state

        frames = stream_run_events(
            run_id=run_id,
            last_seq=parse_last_event_id(last_event_id),
            bus=self._bus,
            subscription=subscription,
            replay=replay,
            state=state,
            heartbeat_s=config.heartbeat_s,
            max_s=min(max_s, config.stream_max_s),
            max_frame_bytes=config.max_frame_bytes,
        )
        return GuardedStream(frames, release)
