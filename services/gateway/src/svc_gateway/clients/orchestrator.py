"""Typed client for M3 (orchestrator): start and read runs, answer, approve, replay events.

M3's contract (PR "M3 Validate workflow"): ``POST /runs``, ``GET /runs/{id}``,
``POST /runs/{id}/answer``, ``POST /runs/{id}/approve``. Event replay,
``GET /runs/{id}/events?after=<seq>`` -> ``{"events": [RunEvent, ...]}``, is *assumed*: M3 does not
store events yet, so a 404 or any failure there only means "no stored events" (live events still
flow through Redis pub/sub).
"""

from typing import Any

from pydantic import ValidationError

from common.errors import DomainError, NotFound, UpstreamError
from common.http import ServiceClient
from common.logging import get_logger
from common.schemas import RunEvent

from ..models.runs import RunView, StepAccepted
from .upstream import UNEXPECTED, guarded, parse

log = get_logger("gateway.orchestrator")
USER_HEADER = "X-User-Id"  # defence in depth: M3 can refuse a run that is not theirs


class OrchestratorClient:
    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    async def start(self, kind: str, user_id: str, run_input: dict[str, Any]) -> str:
        body = {"kind": kind, "user_id": user_id, "input": run_input}
        headers = {USER_HEADER: user_id}
        data = await guarded(lambda: self._client.post_json("/runs", body, headers=headers), "run")
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise UpstreamError(UNEXPECTED)
        return run_id

    async def get(self, run_id: str, user_id: str) -> RunView | None:
        """The live view, or ``None`` when M3 does not know the run (e.g. after a restart)."""
        try:
            headers = {USER_HEADER: user_id}
            data = await guarded(
                lambda: self._client.get_json(f"/runs/{run_id}", headers=headers), "run"
            )
        except NotFound:
            return None
        return parse(RunView, data)

    async def answer(self, run_id: str, user_id: str, question_id: str, answer: float) -> str:
        body = {"question_id": question_id, "answer": answer}
        path = f"/runs/{run_id}/answer"
        return await self._step(path, user_id, body, "the run is not waiting for that")

    async def approve(self, run_id: str, user_id: str, approval_id: str, decision: str) -> str:
        body = {"approval_id": approval_id, "decision": decision}
        path = f"/runs/{run_id}/approve"
        return await self._step(path, user_id, body, "the run is not waiting for that")

    async def _step(self, path: str, user_id: str, body: dict[str, Any], conflict: str) -> str:
        headers = {USER_HEADER: user_id}
        data = await guarded(
            lambda: self._client.post_json(path, body, headers=headers), "run", conflict
        )
        return parse(StepAccepted, data).state

    async def cancel(self, run_id: str, user_id: str) -> None:
        """Best effort: stop a run whose owner record could not be written."""
        headers = {USER_HEADER: user_id}
        try:
            await self._client.post_json(f"/runs/{run_id}/cancel", {}, headers=headers)
        except DomainError as exc:
            log.warning("run_cancel_failed", run_id=run_id, error=type(exc).__name__)

    async def events_after(self, run_id: str, user_id: str, after: int) -> list[RunEvent]:
        try:
            data = await self._client.get_json(
                f"/runs/{run_id}/events", params={"after": after}, headers={USER_HEADER: user_id}
            )
        except DomainError as exc:
            log.info("event_replay_unavailable", run_id=run_id, error=type(exc).__name__)
            return []
        items = data.get("events") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        events: list[RunEvent] = []
        for item in items:
            try:
                events.append(RunEvent.model_validate(item))
            except ValidationError:
                log.warning("event_replay_invalid_entry", run_id=run_id)
        return events

    async def ready(self) -> bool:
        try:
            await self._client.get_json("/ready")
        except DomainError:
            return False
        return True
