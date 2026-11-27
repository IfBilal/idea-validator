"""Run events: ``RunEvent`` objects published per run (docs/harness/05 §2).

M1 turns the Redis channel ``run:{run_id}`` into the SSE stream. Publishing is best-effort: a broken
event bus must never fail a run.
"""

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, Protocol

from common.logging import get_logger
from common.schemas import RunEvent
from common.schemas.trace import RunEventType

log = get_logger("events")


def channel_for(run_id: str) -> str:
    return f"run:{run_id}"


class EventPublisher(Protocol):
    async def publish(self, event: RunEvent) -> None: ...


class InMemoryPublisher:
    """Test double that also serves as a tiny in-process bus."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def publish(self, event: RunEvent) -> None:
        self.events.append(event)

    def events_of(self, run_id: str) -> list[RunEvent]:
        return [e for e in self.events if e.run_id == run_id]


class PubSubRedis(Protocol):
    def publish(self, channel: str, message: str) -> Awaitable[Any]: ...


class RedisPublisher:
    def __init__(self, redis: PubSubRedis) -> None:
        self._redis = redis

    async def publish(self, event: RunEvent) -> None:
        await self._redis.publish(channel_for(event.run_id), event.model_dump_json())


class RunEmitter:
    """Stamps events of one run with a monotonic ``seq`` and a timestamp."""

    def __init__(self, run_id: str, publisher: EventPublisher) -> None:
        self.run_id = run_id
        self._publisher = publisher
        self._seq = 0

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
