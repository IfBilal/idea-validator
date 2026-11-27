"""Run events: monotonic seq per run, one Redis channel per run, never break the run."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from common.schemas import RunEvent
from svc_orchestrator.repositories.events import (
    InMemoryPublisher,
    RedisPublisher,
    RunEmitter,
    channel_for,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[4] / "schemas" / "run_event.schema.json").read_text()
)


async def test_emit_assigns_increasing_seq_starting_at_one() -> None:
    pub = InMemoryPublisher()
    emitter = RunEmitter("run_1", pub)
    first = await emitter.emit("run_started", kind="validate")
    second = await emitter.emit("state_changed", **{"from": "INTAKE", "to": "CACHE_CHECK"})
    assert (first.seq, second.seq) == (1, 2)
    assert pub.events_of("run_1") == [first, second]


async def test_two_runs_have_independent_sequences_and_streams() -> None:
    pub = InMemoryPublisher()
    a, b = RunEmitter("a", pub), RunEmitter("b", pub)
    await a.emit("run_started")
    await a.emit("heartbeat")
    event = await b.emit("run_started")
    assert event.seq == 1
    assert len(pub.events_of("a")) == 2
    assert len(pub.events_of("b")) == 1


async def test_emitted_events_validate_against_the_run_event_schema() -> None:
    pub = InMemoryPublisher()
    event = await RunEmitter("run_1", pub).emit("spend_update", spent={"steps": 1})
    jsonschema.validate(json.loads(event.model_dump_json()), SCHEMA)
    assert event.payload == {"spent": {"steps": 1}}


async def test_payload_never_contains_the_envelope_keys() -> None:
    event = await RunEmitter("r", InMemoryPublisher()).emit("verdict", verdict="GO")
    assert set(event.model_dump()) == {"run_id", "seq", "ts", "type", "payload"}


async def test_a_failing_publisher_does_not_break_the_run() -> None:
    class Broken:
        async def publish(self, event: RunEvent) -> None:
            raise ConnectionError("redis is down")

    emitter = RunEmitter("r", Broken())
    event = await emitter.emit("heartbeat")  # must not raise
    assert event.seq == 1
    assert (await emitter.emit("heartbeat")).seq == 2


def test_channel_name_is_run_colon_id() -> None:
    assert channel_for("run_9") == "run:run_9"


class FakePipe:
    def __init__(self, sent: list[tuple[str, str]]) -> None:
        self.sent = sent

    def rpush(self, *args: Any) -> None: ...
    def set(self, *args: Any, **kwargs: Any) -> None: ...
    def ltrim(self, *args: Any) -> None: ...
    def expire(self, *args: Any) -> None: ...

    def publish(self, channel: str, message: str) -> None:
        self.sent.append((channel, message))

    async def execute(self) -> list[Any]:
        return []


class FakeRedis:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def pipeline(self, transaction: bool = True) -> FakePipe:
        return FakePipe(self.sent)

    async def lrange(self, name: str, start: int, end: int) -> list[bytes]:
        return []

    async def lindex(self, name: str, index: int) -> bytes | None:
        return None

    async def get(self, name: str) -> None:
        return None


async def test_redis_publisher_publishes_json_on_the_run_channel() -> None:
    redis = FakeRedis()
    emitter = RunEmitter("run_7", RedisPublisher(redis))
    await emitter.emit("question", question_id="q1")
    channel, message = redis.sent[0]
    assert channel == "run:run_7"
    decoded: dict[str, Any] = json.loads(message)
    assert decoded["type"] == "question"
    assert decoded["payload"] == {"question_id": "q1"}
    assert RunEvent.model_validate(decoded).seq == 1


@pytest.mark.integration
async def test_redis_pubsub_delivers_the_event(redis_url: str) -> None:
    import asyncio

    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe("run:run_pubsub")
    await pubsub.get_message(timeout=1)  # subscribe confirmation
    await RunEmitter("run_pubsub", RedisPublisher(client)).emit("heartbeat")
    message = None
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if message:
            break
        await asyncio.sleep(0.05)
    await pubsub.aclose()
    await client.aclose()
    assert message is not None
    assert json.loads(message["data"])["type"] == "heartbeat"
