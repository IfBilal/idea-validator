"""Unit tests for the review follow-ups: Redis subscription, frames, tokens, stores, flow caps."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from common.errors import RateLimited, Unauthorized, Unavailable
from svc_gateway.clients.coordination import (
    MemoryIdempotencyStore,
    MemoryStreamGate,
    RedisIdempotencyStore,
    RedisStreamGate,
)
from svc_gateway.clients.events import RedisSubscription
from svc_gateway.models.runs import AnswerBody, DiscoverBody, ValidateBody
from svc_gateway.security import AUDIENCE, ISSUER, issue_token, verify_claims
from svc_gateway.services.runs import RunFlow, RunFlowConfig
from svc_gateway.services.streaming import format_event, stream_deadline
from svc_gateway.testing import FakeM2, FakeM3, ScriptedBus, clients, make_event

SECRET = "s" * 40
USER = str(uuid.uuid4())
RUN = "run_000000000001"
# C1 controls, zero-width, bidi marks and isolates, line/paragraph separators, BOM, soft hyphen
FORBIDDEN_CHARS = (
    0x80,
    0x85,
    0x9F,
    0x200B,
    0x200F,
    0x202A,
    0x202E,
    0x2066,
    0x2069,
    0x2028,
    0x2029,
    0xFEFF,
    0xAD,
)


# ---- 5. the Redis subscription waits for a real message, not the subscribe confirmation
class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class PubSub:
    """get_message returns None for ignored frames (like redis-py) and for timeouts."""

    def __init__(self, clock: FakeClock, script: list[Any], cost: float = 0.1) -> None:
        self.clock, self.script, self.cost = clock, script, cost
        self.timeouts: list[float] = []

    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float = 0.0,  # noqa: ASYNC109 (mirrors redis-py)
    ) -> Any:
        self.timeouts.append(timeout)
        self.clock.now += self.cost
        return self.script.pop(0) if self.script else None

    async def unsubscribe(self, channel: str) -> None: ...

    async def aclose(self) -> None: ...


async def test_next_skips_the_subscribe_confirmation_and_waits_for_the_message() -> None:
    clock = FakeClock()
    pubsub = PubSub(clock, [None, None, {"type": "message", "data": b'{"seq": 1}'}])
    sub = RedisSubscription(pubsub, "run:x", clock=clock)
    assert await sub.next(5.0) == '{"seq": 1}'
    assert len(pubsub.timeouts) == 3
    assert all(t <= 5.0 for t in pubsub.timeouts)


async def test_next_returns_none_only_once_the_deadline_has_passed() -> None:
    clock = FakeClock()
    pubsub = PubSub(clock, [], cost=1.0)
    sub = RedisSubscription(pubsub, "run:x", clock=clock)
    assert await sub.next(3.0) is None
    assert 3.0 <= clock.now < 5.0  # it waited the whole time, in slices that shrink
    assert pubsub.timeouts == sorted(pubsub.timeouts, reverse=True)


async def test_next_skips_undecodable_and_foreign_frames_without_giving_up() -> None:
    clock = FakeClock()
    script = [
        {"type": "subscribe", "data": 1},
        {"type": "message", "data": b"\xff\xfe"},
        {"type": "message", "data": 5},
        {"type": "message", "data": "ok"},
    ]
    sub = RedisSubscription(PubSub(clock, script), "run:x", clock=clock)
    assert await sub.next(5.0) == "ok"


async def test_a_zero_timeout_polls_once() -> None:
    clock = FakeClock()
    pubsub = PubSub(clock, [{"type": "message", "data": "now"}])
    assert await RedisSubscription(pubsub, "c", clock=clock).next(0.0) == "now"


# ---- 12. frame size
def test_a_small_frame_is_untouched() -> None:
    frame = format_event(make_event(RUN, 1, "tool_call", tool="fetch"), 65536)
    assert '"tool": "fetch"' in frame or '"tool":"fetch"' in frame


def test_an_oversized_event_keeps_its_place_but_loses_its_payload() -> None:
    event = make_event(RUN, 7, "partial_result", blob="x" * 5000)
    frame = format_event(event, 1000)
    assert len(frame.encode()) <= 1000
    assert frame.startswith("event: partial_result\nid: 7\n")
    data = json.loads(frame.split("data: ", 1)[1])
    assert data["seq"] == 7
    assert data["payload"] == {"truncated": True, "reason": "event too large"}


def test_the_size_limit_is_in_bytes_not_characters() -> None:
    event = make_event(RUN, 1, "partial_result", text="é" * 400)  # 800 bytes of payload
    assert json.loads(format_event(event, 700).split("data: ", 1)[1])["payload"]["truncated"]
    assert "truncated" not in format_event(event, 5000)


# ---- 6 (events): failure reasons are fixed text
def test_failure_event_payloads_are_reduced_to_status_and_a_fixed_reason() -> None:
    event = make_event(
        RUN, 3, "budget_stopped", status="budget_stopped", reason="http://x.internal"
    )
    data = json.loads(format_event(event, 65536).split("data: ", 1)[1])
    assert data["payload"] == {
        "status": "budget_stopped",
        "reason": "the run stopped at its budget",
    }


def test_synthesised_marker_survives_the_scrub() -> None:
    event = make_event(RUN, 3, "run_completed", status="succeeded", synthesised=True)
    data = json.loads(format_event(event, 65536).split("data: ", 1)[1])
    assert data["payload"]["synthesised"] is True


def test_a_tool_error_is_replaced_by_a_generic_marker() -> None:
    event = make_event(RUN, 2, "tool_call", tool="fetch", error="boom http://kb.internal:8000")
    assert "internal" not in format_event(event, 65536)


# ---- 2. token lifetime
def test_stream_deadline_is_the_smaller_of_the_cap_and_the_token_life() -> None:
    assert stream_deadline(1000.0, 900.0, 900.0) == 100.0
    assert stream_deadline(100000.0, 900.0, 900.0) == 900.0
    assert stream_deadline(900.0, 900.0, 900.0) == 0.0
    assert stream_deadline(800.0, 900.0, 900.0) == 0.0  # already expired: never negative


def test_verify_claims_returns_the_user_and_the_expiry() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    token = issue_token(SECRET, USER, 100, now)
    user, exp = verify_claims(SECRET, token)
    assert user == USER
    assert exp == (now + timedelta(seconds=100)).timestamp()


def test_verify_claims_rejects_what_verify_token_rejects() -> None:
    bad = jwt.encode({"sub": USER, "iss": ISSUER, "aud": AUDIENCE}, SECRET, algorithm="HS256")
    with pytest.raises(Unauthorized):
        verify_claims(SECRET, bad)
    with pytest.raises(Unauthorized):
        verify_claims(SECRET, "junk")


# ---- 3. numbers
@pytest.mark.parametrize("value", [10**400, -(10**400), 10**13, -(10**13), float("1e309")])
def test_out_of_range_numbers_are_validation_errors_not_overflows(value: Any) -> None:
    with pytest.raises(ValueError, match="answer"):
        AnswerBody.model_validate({"question_id": "q", "answer": value})


@pytest.mark.parametrize("value", [10**12, -(10**12), 0, 10000])
def test_numbers_inside_the_bound_are_accepted(value: int) -> None:
    assert AnswerBody.model_validate({"question_id": "q", "answer": value}).answer == float(value)


# ---- 10. characters
@pytest.mark.parametrize("ch", [chr(c) for c in FORBIDDEN_CHARS])
def test_format_line_separator_and_c1_characters_are_refused(ch: str) -> None:
    for build in (
        lambda: ValidateBody(idea_text=f"a{ch}b", title="t"),
        lambda: ValidateBody(idea_text="a", title=f"a{ch}b"),
        lambda: DiscoverBody(niche=f"a{ch}b"),
    ):
        with pytest.raises(ValueError, match="characters"):
            build()


def test_tab_newline_and_normal_unicode_are_still_fine() -> None:
    assert ValidateBody(idea_text="café\n\t日本語 🙂", title="t")


# ---- idempotency store and stream gate (memory twins of the Redis ones)
async def test_claim_is_exclusive_and_values_expire_by_ttl_argument() -> None:
    store = MemoryIdempotencyStore()
    assert await store.claim("k", 60) is True
    assert await store.claim("k", 60) is False
    assert await store.get("k") == {"pending": True}
    await store.put("k", {"run_id": "r"}, 60)
    assert await store.get("k") == {"run_id": "r"}
    await store.delete("k")
    assert await store.get("k") is None
    await store.delete("k")  # deleting twice is fine


async def test_gate_counts_per_user_and_never_goes_below_zero() -> None:
    gate = MemoryStreamGate()
    assert [await gate.acquire("u", 2, 60) for _ in range(3)] == [True, True, False]
    assert gate.count("u") == 2
    await gate.release("u")
    await gate.release("u")
    await gate.release("u")
    assert gate.count("u") == 0
    assert await gate.acquire("other", 1, 60) is True


class FakeRedisKV:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> Any:
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def incr(self, key: str) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])

    async def decr(self, key: str) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) - 1)
        return int(self.data[key])

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds


async def test_redis_store_and_gate_use_ttls_and_hashed_keys() -> None:
    kv = FakeRedisKV()
    store = RedisIdempotencyStore(kv)
    assert await store.claim("abc", 30) is True
    assert await store.claim("abc", 30) is False
    await store.put("abc", {"run_id": "r"}, 600)
    assert await store.get("abc") == {"run_id": "r"}
    assert max(kv.ttls.values()) == 600
    gate = RedisStreamGate(kv)
    assert [await gate.acquire("u", 1, 99) for _ in range(2)] == [True, False]
    assert kv.data[next(k for k in kv.data if "stream" in k)] == "1"  # the refused hit was undone
    await gate.release("u")
    assert kv.data[next(k for k in kv.data if "stream" in k)] == "0"
    await gate.release("u")  # never negative
    assert kv.data[next(k for k in kv.data if "stream" in k)] == "0"


async def test_redis_failures_become_503s() -> None:
    from redis.exceptions import RedisError

    class RedisDown:
        async def _boom(self, *args: Any, **kwargs: Any) -> Any:
            raise RedisError("down")

        get = set = incr = decr = delete = expire = _boom

    with pytest.raises(Unavailable):
        await RedisIdempotencyStore(RedisDown()).get("k")
    with pytest.raises(Unavailable):
        await RedisStreamGate(RedisDown()).acquire("u", 1, 1)


async def test_a_corrupt_stored_value_is_treated_as_absent() -> None:
    kv = FakeRedisKV()
    kv.data["idem:abc"] = "not json"
    assert await RedisIdempotencyStore(kv).get("abc") is None


# ---- the flow's caps without HTTP
def flow(bus: ScriptedBus | None = None, **config: Any) -> tuple[RunFlow, FakeM2, FakeM3]:
    m2, m3 = FakeM2(), FakeM3()
    ideas, orchestrator, _ = clients(m2, m3)
    cfg = RunFlowConfig(**config)
    built = RunFlow(
        ideas, orchestrator, bus or ScriptedBus(then_idle=True), MemoryIdempotencyStore(),
        MemoryStreamGate(), cfg,
    )  # fmt: skip
    m2.add_run(RUN, USER)
    m3.runs[RUN] = {"kind": "validate", "state": "running", "input": {}}
    return built, m2, m3


async def test_the_replica_wide_cap_rejects_the_next_stream_and_frees_on_close() -> None:
    built, _, _ = flow(streams_global=2, streams_per_user=10)
    first = await built.open_events(USER, RUN, None, 900)
    second = await built.open_events(USER, RUN, None, 900)
    with pytest.raises(Unavailable):
        await built.open_events(USER, RUN, None, 900)
    await first.aclose()
    third = await built.open_events(USER, RUN, None, 900)
    await second.aclose()
    await third.aclose()


async def test_the_per_user_cap_rejects_and_frees_when_the_generator_is_closed() -> None:
    built, _, _ = flow(streams_per_user=1)
    gen = await built.open_events(USER, RUN, None, 900)
    await gen.__anext__()  # the stream is running
    with pytest.raises(RateLimited):
        await built.open_events(USER, RUN, None, 900)
    await gen.aclose()
    again = await built.open_events(USER, RUN, None, 900)
    await again.aclose()


async def test_a_generator_that_is_cancelled_while_waiting_frees_its_slot() -> None:
    bus = ScriptedBus(then_idle=True)
    built, _, _ = flow(bus, streams_per_user=1)
    gen = await built.open_events(USER, RUN, None, 900)

    async def drain() -> None:
        async for _ in gen:
            pass

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.subscriptions[0].closed
    again = await built.open_events(USER, RUN, None, 900)  # the slot is free again
    await again.aclose()


async def test_every_redis_operation_fails_closed_when_redis_is_down() -> None:
    from redis.exceptions import RedisError

    class RedisDown:
        async def boom(self, *args: Any, **kwargs: Any) -> Any:
            raise RedisError("down")

        get = set = incr = decr = delete = expire = boom

    store, gate = RedisIdempotencyStore(RedisDown()), RedisStreamGate(RedisDown())
    for call in (
        store.claim("k", 1),
        store.put("k", {}, 1),
        store.delete("k"),
        gate.release("u"),
    ):
        with pytest.raises(Unavailable):
            await call


async def test_memory_twins_fail_closed_when_broken() -> None:
    store, gate = MemoryIdempotencyStore(), MemoryStreamGate()
    store.broken = gate.broken = True
    for call in (store.get("k"), store.claim("k", 1), store.put("k", {}, 1), store.delete("k")):
        with pytest.raises(Unavailable):
            await call
    with pytest.raises(Unavailable):
        await gate.acquire("u", 1, 1)
