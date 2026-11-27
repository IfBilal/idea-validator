"""Stored run events: replay after a restart and a sequence that continues (docs/harness/07 §1)."""

import json
from typing import Any

import pytest

from svc_orchestrator.repositories.events import (
    MAX_STORED_EVENTS,
    InMemoryPublisher,
    RedisPublisher,
    RunEmitter,
    channel_for,
    events_key,
)


async def test_stored_events_are_read_back_after_a_sequence_number() -> None:
    pub = InMemoryPublisher()
    emitter = RunEmitter("run_1", pub)
    for _ in range(5):
        await emitter.emit("heartbeat")
    assert [e.seq for e in await pub.read("run_1", 0)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in await pub.read("run_1", 3)] == [4, 5]
    assert await pub.read("run_1", 5) == []
    assert await pub.read("run_1", 99) == []
    assert await pub.read("other", 0) == []


async def test_the_last_sequence_number_of_a_run_is_known() -> None:
    pub = InMemoryPublisher()
    assert await pub.last_seq("run_1") == 0
    await RunEmitter("run_1", pub).emit("heartbeat")
    assert await pub.last_seq("run_1") == 1


async def test_an_emitter_can_continue_after_the_last_stored_sequence_number() -> None:
    pub = InMemoryPublisher()
    await RunEmitter("run_1", pub).emit("run_started")
    again = RunEmitter("run_1", pub)
    again.resume_from(await pub.last_seq("run_1"))
    assert (await again.emit("heartbeat")).seq == 2


async def test_resume_from_never_moves_the_sequence_backwards() -> None:
    emitter = RunEmitter("run_1", InMemoryPublisher())
    for _ in range(3):
        await emitter.emit("heartbeat")
    emitter.resume_from(1)
    assert (await emitter.emit("heartbeat")).seq == 4
    with pytest.raises(ValueError):
        emitter.resume_from(-1)


class FakePipe:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log

    def rpush(self, *args: Any) -> "FakePipe":
        self.log.append(("rpush", *args))
        return self

    def ltrim(self, *args: Any) -> "FakePipe":
        self.log.append(("ltrim", *args))
        return self

    def expire(self, *args: Any) -> "FakePipe":
        self.log.append(("expire", *args))
        return self

    def publish(self, *args: Any) -> "FakePipe":
        self.log.append(("publish", *args))
        return self

    async def execute(self) -> list[Any]:
        return []


class FakeStoreRedis:
    def __init__(self) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.items: list[bytes] = []

    def pipeline(self, transaction: bool = True) -> FakePipe:
        return FakePipe(self.log)

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        return self.items

    async def lindex(self, key: str, index: int) -> bytes | None:
        return self.items[index] if self.items else None


async def test_the_redis_publisher_stores_before_it_publishes_and_bounds_the_list() -> None:
    redis = FakeStoreRedis()
    event = await RunEmitter("run_1", RedisPublisher(redis)).emit("heartbeat")
    ops = [entry[0] for entry in redis.log]
    assert ops == ["rpush", "ltrim", "expire", "publish"]  # a stored event is never missing
    assert redis.log[0][1] == events_key("run_1") == "run:run_1:events"
    assert redis.log[1][2:] == (-MAX_STORED_EVENTS, -1)
    assert redis.log[3][1] == channel_for("run_1")
    assert json.loads(redis.log[3][2])["seq"] == event.seq


async def test_the_redis_log_skips_malformed_entries_and_filters_by_seq() -> None:
    redis = FakeStoreRedis()
    good = await RunEmitter("run_1", InMemoryPublisher()).emit("heartbeat")
    later = good.model_copy(update={"seq": 2})
    other_run = good.model_copy(update={"run_id": "intruder", "seq": 3})
    redis.items = [
        b"not json",
        good.model_dump_json().encode(),
        b'{"seq": "x"}',
        later.model_dump_json().encode(),
        other_run.model_dump_json().encode(),
    ]
    log = RedisPublisher(redis)
    assert [e.seq for e in await log.read("run_1", 0)] == [1, 2]
    assert [e.seq for e in await log.read("run_1", 1)] == [2]
    redis.items = redis.items[:4]
    assert await log.last_seq("run_1") == 2
    redis.items = []
    assert await log.last_seq("run_1") == 0
    redis.items = [b"garbage"]
    assert await log.last_seq("run_1") == 0
