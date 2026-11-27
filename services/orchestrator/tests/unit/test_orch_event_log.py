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
    seq_key,
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
    def __init__(self, redis: "FakeStoreRedis") -> None:
        self.redis = redis

    def _log(self, *args: Any) -> "FakePipe":
        self.redis.log.append(args)
        return self

    def rpush(self, *args: Any) -> "FakePipe":
        self.redis.items.append(args[1].encode())
        return self._log("rpush", *args)

    def ltrim(self, *args: Any) -> "FakePipe":
        return self._log("ltrim", *args)

    def expire(self, *args: Any) -> "FakePipe":
        return self._log("expire", *args)

    def set(self, key: str, value: Any, ex: int | None = None) -> "FakePipe":
        self.redis.kv[key] = str(value)
        return self._log("set", key, value, ex)

    def publish(self, *args: Any) -> "FakePipe":
        return self._log("publish", *args)

    async def execute(self) -> list[Any]:
        return []


class FakeStoreRedis:
    def __init__(self) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.items: list[bytes] = []
        self.kv: dict[str, str] = {}
        self.ranges: list[tuple[int, int]] = []

    def pipeline(self, transaction: bool = True) -> FakePipe:
        return FakePipe(self)

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        self.ranges.append((start, stop))
        return self.items[start:] if start < 0 else self.items[start : stop + 1]

    async def lindex(self, key: str, index: int) -> bytes | None:
        return self.items[index] if self.items else None

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)


async def test_the_redis_publisher_stores_before_it_publishes_and_bounds_the_list() -> None:
    redis = FakeStoreRedis()
    event = await RunEmitter("run_1", RedisPublisher(redis)).emit("heartbeat")
    ops = [entry[0] for entry in redis.log]
    assert ops == ["rpush", "ltrim", "expire", "set", "publish"]  # a stored event is never missing
    assert redis.log[0][1] == events_key("run_1") == "run:run_1:events"
    assert redis.log[1][2:] == (-MAX_STORED_EVENTS, -1)
    assert redis.log[3][1:3] == (seq_key("run_1"), 1)
    assert redis.log[4][1] == channel_for("run_1")
    assert json.loads(redis.log[4][2])["seq"] == event.seq


async def _filled(count: int) -> tuple[FakeStoreRedis, RedisPublisher]:
    redis = FakeStoreRedis()
    publisher = RedisPublisher(redis)
    emitter = RunEmitter("run_1", publisher)
    for _ in range(count):
        await emitter.emit("heartbeat")
    return redis, publisher


async def test_a_poll_reads_only_the_tail_of_the_list_not_all_of_it() -> None:
    redis, publisher = await _filled(500)
    redis.ranges.clear()
    assert [e.seq for e in await publisher.read("run_1", 497)] == [498, 499, 500]
    assert redis.ranges == [(-3, -1)]


async def test_a_poll_with_nothing_new_does_not_read_the_list_at_all() -> None:
    redis, publisher = await _filled(5)
    redis.ranges.clear()
    assert await publisher.read("run_1", 5) == []
    assert await publisher.read("run_1", 50) == []
    assert redis.ranges == []


async def test_the_tail_read_is_capped_at_the_list_bound() -> None:
    redis, publisher = await _filled(3)
    redis.kv[seq_key("run_1")] = str(10 * MAX_STORED_EVENTS)
    redis.ranges.clear()
    await publisher.read("run_1", 0)
    assert redis.ranges == [(-MAX_STORED_EVENTS, -1)]


async def test_the_sequence_continues_after_the_event_list_was_lost() -> None:
    redis, publisher = await _filled(7)
    redis.items.clear()  # the list expired or was evicted; the counter key survived
    assert await publisher.last_seq("run_1") == 7
    assert await publisher.read("run_1", 0) == []


async def test_the_sequence_continues_after_the_counter_was_lost() -> None:
    redis, publisher = await _filled(7)
    redis.kv.clear()
    assert await publisher.last_seq("run_1") == 7  # the last stored event says so


async def test_a_garbage_counter_or_tail_is_ignored() -> None:
    redis, publisher = await _filled(3)
    redis.kv[seq_key("run_1")] = "not a number"
    redis.items.append(b"garbage")
    assert await publisher.last_seq("run_1") == 0
    redis.kv.clear()
    redis.items = []
    assert await publisher.last_seq("run_1") == 0


async def test_the_redis_log_skips_malformed_entries_and_filters_by_seq() -> None:
    redis = FakeStoreRedis()
    good = await RunEmitter("run_1", InMemoryPublisher()).emit("heartbeat")
    later = good.model_copy(update={"seq": 2})
    other_run = good.model_copy(update={"run_id": "intruder", "seq": 3})
    redis.kv[seq_key("run_1")] = "4"
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
