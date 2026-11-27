"""Run events: ``RunEvent`` objects published per run (docs/harness/05 §2).

M1 turns the Redis channel ``run:{run_id}`` into the SSE stream. Publishing is best-effort: a broken
event bus must never fail a run.

Every event is also appended to a bounded Redis list (``run:{run_id}:events``) *before* it is
published, so a client that reconnects, or M3 itself after a restart, can read what happened:
``GET /runs/{id}/events?after=<seq>`` and the emitter's sequence continuing where it stopped.
"""

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from common.logging import get_logger
from common.schemas import RunEvent
from common.schemas.trace import RunEventType

log = get_logger("events")


MAX_STORED_EVENTS = 2000  # a run emits a few hundred; the cap only bounds a runaway one
EVENTS_TTL_S = 7 * 24 * 3600  # same as the budget ledger


def channel_for(run_id: str) -> str:
    return f"run:{run_id}"


def events_key(run_id: str) -> str:
    return f"run:{run_id}:events"


class EventPublisher(Protocol):
    async def publish(self, event: RunEvent) -> None: ...


class EventLog(Protocol):
    async def read(self, run_id: str, after: int = 0) -> list[RunEvent]: ...

    async def last_seq(self, run_id: str) -> int: ...


class InMemoryPublisher:
    """Test double that also serves as a tiny in-process bus."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def publish(self, event: RunEvent) -> None:
        self.events.append(event)

    def events_of(self, run_id: str) -> list[RunEvent]:
        return [e for e in self.events if e.run_id == run_id]

    async def read(self, run_id: str, after: int = 0) -> list[RunEvent]:
        return [e for e in self.events_of(run_id) if e.seq > after]

    async def last_seq(self, run_id: str) -> int:
        return max((e.seq for e in self.events_of(run_id)), default=0)


class EventRedis(Protocol):
    def pipeline(self, transaction: bool = True) -> Any: ...
    def lrange(self, name: str, start: int, end: int) -> Awaitable[Any]: ...


class RedisPublisher:
    """Publishes to the run's channel and keeps the events for replay."""

    def __init__(self, redis: EventRedis) -> None:
        self._redis = redis

    async def publish(self, event: RunEvent) -> None:
        payload, key = event.model_dump_json(), events_key(event.run_id)
        pipe = self._redis.pipeline(transaction=True)
        pipe.rpush(key, payload)
        pipe.ltrim(key, -MAX_STORED_EVENTS, -1)
        pipe.expire(key, EVENTS_TTL_S)
        pipe.publish(channel_for(event.run_id), payload)  # after the store: replay never misses it
        await pipe.execute()

    async def read(self, run_id: str, after: int = 0) -> list[RunEvent]:
        raw = await self._redis.lrange(events_key(run_id), 0, -1)
        events: list[RunEvent] = []
        for item in raw:
            try:
                event = RunEvent.model_validate_json(item)
            except ValidationError:
                log.warning("stored_event_malformed", run_id=run_id)
                continue
            if event.run_id == run_id and event.seq > after:
                events.append(event)
        return events

    async def last_seq(self, run_id: str) -> int:
        return max((e.seq for e in await self.read(run_id)), default=0)


class RunEmitter:
    """Stamps events of one run with a monotonic ``seq`` and a timestamp."""

    def __init__(self, run_id: str, publisher: EventPublisher) -> None:
        self.run_id = run_id
        self._publisher = publisher
        self._seq = 0

    def resume_from(self, seq: int) -> None:
        """Continue after ``seq`` (the last stored event) so a restart never repeats a number."""
        if seq < 0:
            raise ValueError("a sequence number cannot be negative")
        self._seq = max(self._seq, seq)

    async def emit(self, type_: RunEventType, **payload: Any) -> RunEvent:
        self._seq += 1
        event = RunEvent(
            run_id=self.run_id, seq=self._seq, ts=datetime.now(UTC), type=type_, payload=payload
        )
        try:
            await self._publisher.publish(event)
        except Exception as exc:
            log.warning("event_publish_failed", run_id=self.run_id, error=type(exc).__name__)
        return event


class QuietEmitter(RunEmitter):
    """Emitter for a Discover child: progress events pass through, lifecycle events are dropped.

    A child is not a run: only the parent announces ``run_completed``, ``verdict`` and so on.
    Events share the parent's ``seq`` so the stream stays monotonic.
    """

    DROPPED: frozenset[str] = frozenset(
        {
            "state_changed",
            "verdict",
            "run_completed",
            "run_failed",
            "budget_stopped",
            "question",
            "approval_required",
        }
    )

    def __init__(self, parent: RunEmitter) -> None:
        super().__init__(parent.run_id, parent._publisher)
        self._parent = parent

    async def emit(self, type_: RunEventType, **payload: Any) -> RunEvent:
        if type_ in self.DROPPED:
            return RunEvent(
                run_id=self.run_id, seq=0, ts=datetime.now(UTC), type=type_, payload=payload
            )
        return await self._parent.emit(type_, **payload)
