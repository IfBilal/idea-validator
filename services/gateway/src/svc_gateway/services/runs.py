"""Starting, reading and following runs. M2 decides who owns what; M3 does the work.

The gateway never computes, edits or rounds a verdict: ``result`` and ``card`` are passed through
exactly as M3 and M2 returned them (CLAUDE.md rule 1).
"""

from collections.abc import AsyncGenerator
from typing import Any

from common.errors import Conflict, NotFound
from common.logging import get_logger

from ..clients.events import EventBus
from ..clients.ideas import IdeaClient
from ..clients.orchestrator import OrchestratorClient
from ..models.runs import (
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
from .streaming import parse_last_event_id, stream_run_events

log = get_logger("gateway.runs")


class RunFlow:
    def __init__(
        self,
        ideas: IdeaClient,
        orchestrator: OrchestratorClient,
        bus: EventBus,
        heartbeat_s: float,
        stream_max_s: float,
    ) -> None:
        self._ideas = ideas
        self._orchestrator = orchestrator
        self._bus = bus
        self._heartbeat_s = heartbeat_s
        self._stream_max_s = stream_max_s

    # ---- starting runs -----------------------------------------------------------------------
    async def validate(self, user_id: str, body: ValidateBody) -> ValidateAccepted:
        if body.idea_id is None:
            assert body.title is not None  # the body model guarantees it
            created = await self._ideas.create_idea(user_id, body.title, body.idea_text)
            idea_id, version = created.idea_id, created.version
        else:
            idea_id = str(body.idea_id)
            existing = await self._ideas.versions(idea_id, user_id)  # 404 unless it is theirs
            latest = max(existing, key=lambda v: v.version, default=None)
            if latest is not None and latest.text == body.idea_text:
                version = latest.version  # unchanged text: same version, so the cache can answer
            else:
                version = (await self._ideas.add_version(idea_id, user_id, body.idea_text)).version
        run_input: dict[str, Any] = {
            "idea_text": body.idea_text,
            "idea_id": idea_id,
            "version": version,
            "title": body.title,
        }
        run_id = await self._start(user_id, "validate", run_input)
        return ValidateAccepted(run_id=run_id, idea_id=idea_id, version=version)

    async def discover(self, user_id: str, body: DiscoverBody) -> RunAccepted:
        run_input = {"niche": body.niche, "count": body.count, "deep_search": body.deep_search}
        return RunAccepted(run_id=await self._start(user_id, "discover", run_input))

    async def challenge(self, user_id: str, idea_id: str, body: ChallengeBody) -> RunAccepted:
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
        return RunAccepted(run_id=await self._start(user_id, "challenge", run_input))

    async def whatif(self, user_id: str, idea_id: str, body: WhatIfBody) -> RunAccepted:
        if (await self._ideas.detail(idea_id, user_id, body.version)).card is None:
            raise Conflict("this version has no verdict yet")
        run_input: dict[str, Any] = {"idea_id": idea_id, "version": body.version}
        if body.change_id is not None:
            run_input["change_id"] = body.change_id
        else:
            run_input["custom_change"] = body.custom_change
        return RunAccepted(run_id=await self._start(user_id, "whatif", run_input))

    async def _start(self, user_id: str, kind: str, run_input: dict[str, Any]) -> str:
        run_id = await self._orchestrator.start(kind, user_id, run_input)
        # the owner is recorded at once, so no one else can ever read this run
        await self._ideas.put_run(run_id, user_id, kind, "running", run_input)
        log.info("run_started", run_id=run_id, kind=kind)
        return run_id

    # ---- reading and steering a run ----------------------------------------------------------
    async def get(self, user_id: str, run_id: str) -> RunView:
        record = await self._ideas.get_run(run_id, user_id)
        live = await self._orchestrator.get(run_id)
        if live is not None:
            return live
        return RunView(  # M3 forgot the run (restart): the stored record is all there is
            run_id=run_id,
            kind=record.kind,
            state=record.state,
            progress={},
            spent={},
            budget={},
            pending_question=None,
            pending_approval=None,
            result=None,
        )

    async def answer(self, user_id: str, run_id: str, body: AnswerBody) -> StepAccepted:
        await self._ideas.get_run(run_id, user_id)
        state = await self._orchestrator.answer(run_id, body.question_id, body.answer)
        return StepAccepted(run_id=run_id, state=state)

    async def approve(self, user_id: str, run_id: str, body: ApproveBody) -> StepAccepted:
        await self._ideas.get_run(run_id, user_id)
        state = await self._orchestrator.approve(run_id, body.approval_id, body.decision)
        return StepAccepted(run_id=run_id, state=state)

    # ---- the event stream --------------------------------------------------------------------
    async def open_events(
        self, user_id: str, run_id: str, last_event_id: str | None
    ) -> AsyncGenerator[str]:
        """Check ownership now (so a 404 is a normal JSON error), return the frame generator."""
        record = await self._ideas.get_run(run_id, user_id)

        async def replay(after: int) -> list[Any]:
            return await self._orchestrator.events_after(run_id, after)

        async def state() -> str | None:
            live = await self._orchestrator.get(run_id)
            return live.state if live is not None else record.state

        return stream_run_events(
            run_id=run_id,
            last_seq=parse_last_event_id(last_event_id),
            bus=self._bus,
            replay=replay,
            state=state,
            heartbeat_s=self._heartbeat_s,
            max_s=self._stream_max_s,
        )
