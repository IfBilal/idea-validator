"""The SSE generator, tested directly with a scripted bus (no HTTP, no real Redis)."""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import pytest

from common.schemas import RunEvent
from svc_gateway.services.streaming import format_event, parse_last_event_id, stream_run_events
from svc_gateway.testing import ScriptedBus, make_event

RUN = "run_000000000001"


def wire(event: RunEvent) -> str:
    return event.model_dump_json()


class Source:
    """Stored events (M3 replay) and the run state, with call recording."""

    def __init__(self, stored: list[RunEvent] | None = None, state: str | None = "running") -> None:
        self.stored = stored or []
        self.state = state
        self.replays: list[int] = []

    async def replay(self, after: int) -> list[RunEvent]:
        self.replays.append(after)
        return [e for e in self.stored if e.seq > after]

    async def current_state(self) -> str | None:
        return self.state


def stream(
    bus: ScriptedBus,
    source: Source,
    last_seq: int = 0,
    heartbeat_s: float = 15.0,
    max_s: float = 3600.0,
    clock: Any = None,
) -> AsyncGenerator[str]:
    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    return stream_run_events(
        run_id=RUN,
        last_seq=last_seq,
        bus=bus,
        replay=source.replay,
        state=source.current_state,
        heartbeat_s=heartbeat_s,
        max_s=max_s,
        **kwargs,
    )


async def collect(gen: AsyncIterator[str]) -> list[str]:
    return [frame async for frame in gen]


def events_in(frames: list[str]) -> list[dict[str, Any]]:
    out = []
    for frame in frames:
        data = [line for line in frame.splitlines() if line.startswith("data: ")]
        if data:
            out.append(json.loads(data[0][6:]))
    return out


# ---- wire format
def test_frame_has_event_id_and_one_line_of_json() -> None:
    frame = format_event(make_event(RUN, 7, "tool_call", tool="fetch", note="a\nb\r\nc"))
    lines = frame.split("\n")
    assert lines[0] == "event: tool_call"
    assert lines[1] == "id: 7"
    assert lines[2].startswith("data: ")
    assert frame.endswith("\n\n")
    assert frame.count("\n") == 4  # no raw newline can come out of the payload
    assert json.loads(lines[2][6:])["payload"]["note"] == "a\nb\r\nc"


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        (None, 0),
        ("", 0),
        ("0", 0),
        ("5", 5),
        (" 12 ", 12),
        ("-1", 0),
        ("abc", 0),
        ("1.5", 0),
        ("5; drop", 0),
        ("9" * 30, 0),
        ("١٢", 0),  # non-ASCII digits are not a sequence number
    ],
)
def test_last_event_id_parsing_is_forgiving_but_never_negative(raw: str | None, value: int) -> None:
    assert parse_last_event_id(raw) == value


# ---- live events
async def test_live_events_are_forwarded_in_order_until_the_run_completes() -> None:
    live = [
        wire(make_event(RUN, 1, "run_started")),
        wire(make_event(RUN, 2, "state_changed")),
        wire(make_event(RUN, 3, "run_completed", status="succeeded")),
    ]
    frames = await collect(stream(ScriptedBus(live), Source()))
    assert [e["seq"] for e in events_in(frames)] == [1, 2, 3]
    assert [e["type"] for e in events_in(frames)][-1] == "run_completed"


@pytest.mark.parametrize("terminal", ["run_completed", "run_failed", "budget_stopped"])
async def test_each_terminal_type_closes_the_stream(terminal: str) -> None:
    bus = ScriptedBus([wire(make_event(RUN, 1, terminal)), wire(make_event(RUN, 2))])
    frames = await collect(stream(bus, Source()))
    assert [e["seq"] for e in events_in(frames)] == [1]  # nothing after the terminal event
    assert bus.subscriptions[0].closed


async def test_the_subscription_is_taken_before_replay_so_nothing_is_lost() -> None:
    bus = ScriptedBus([wire(make_event(RUN, 2, "run_completed"))])
    source = Source([make_event(RUN, 1)])
    frames = await collect(stream(bus, source))
    assert [e["seq"] for e in events_in(frames)] == [1, 2]
    assert bus.channels == [RUN]


# ---- replay and Last-Event-ID
async def test_replay_comes_first_then_live_events() -> None:
    source = Source([make_event(RUN, 1), make_event(RUN, 2)])
    bus = ScriptedBus([wire(make_event(RUN, 3, "run_completed"))])
    frames = await collect(stream(bus, source))
    assert [e["seq"] for e in events_in(frames)] == [1, 2, 3]
    assert source.replays[0] == 0


async def test_resume_skips_what_the_client_already_has() -> None:
    source = Source([make_event(RUN, n) for n in (1, 2, 3, 4)])
    bus = ScriptedBus([wire(make_event(RUN, 5, "run_completed"))])
    frames = await collect(stream(bus, source, last_seq=2))
    assert [e["seq"] for e in events_in(frames)] == [3, 4, 5]
    assert source.replays[0] == 2


async def test_live_duplicates_and_old_events_are_dropped() -> None:
    source = Source([make_event(RUN, 1), make_event(RUN, 2)])
    live = [
        wire(make_event(RUN, 2)),  # also in the replay
        wire(make_event(RUN, 1)),  # older than what was sent
        wire(make_event(RUN, 3)),
        wire(make_event(RUN, 3)),  # duplicate delivery
        wire(make_event(RUN, 4, "run_completed")),
    ]
    frames = await collect(stream(ScriptedBus(live), source))
    assert [e["seq"] for e in events_in(frames)] == [1, 2, 3, 4]


async def test_last_event_id_beyond_the_latest_gets_only_newer_events() -> None:
    bus = ScriptedBus([wire(make_event(RUN, 5)), wire(make_event(RUN, 51, "run_completed"))])
    frames = await collect(stream(bus, Source(), last_seq=50))
    assert [e["seq"] for e in events_in(frames)] == [51]


class LateStorage(Source):
    """Nothing stored when the stream opens; the events appear in storage afterwards."""

    def __init__(self, later: list[RunEvent], state: str | None = "running") -> None:
        super().__init__([], state)
        self.later = later

    async def replay(self, after: int) -> list[RunEvent]:
        self.replays.append(after)
        if len(self.replays) == 1:
            return []
        return [e for e in self.later if e.seq > after]


async def test_a_gap_in_the_live_sequence_is_filled_from_the_stored_events() -> None:
    source = LateStorage([make_event(RUN, n) for n in (1, 2, 3)])
    bus = ScriptedBus([wire(make_event(RUN, 1)), wire(make_event(RUN, 3, "run_completed"))])
    frames = await collect(stream(bus, source))
    assert [e["seq"] for e in events_in(frames)] == [1, 2, 3]


async def test_gap_filling_stops_at_a_stored_terminal_event() -> None:
    later = [make_event(RUN, 1), make_event(RUN, 2, "run_failed"), make_event(RUN, 3)]
    bus = ScriptedBus([wire(make_event(RUN, 3))])
    frames = await collect(stream(bus, LateStorage(later)))
    assert [e["seq"] for e in events_in(frames)] == [1, 2]


async def test_gap_filling_does_not_pass_the_live_event() -> None:
    later = [make_event(RUN, 1), make_event(RUN, 2), make_event(RUN, 3, "run_completed")]
    bus = ScriptedBus([wire(make_event(RUN, 2)), wire(make_event(RUN, 3, "run_completed"))])
    frames = await collect(stream(bus, LateStorage(later)))
    assert [e["seq"] for e in events_in(frames)] == [1, 2, 3]


async def test_a_terminal_event_stored_just_after_the_state_check_is_not_duplicated() -> None:
    later = [make_event(RUN, 1), make_event(RUN, 2, "run_completed")]
    frames = await collect(stream(ScriptedBus(then_idle=True), LateStorage(later, "succeeded")))
    assert [(e["seq"], e["type"]) for e in events_in(frames)] == [
        (1, "agent_started"),
        (2, "run_completed"),
    ]


async def test_replay_with_a_terminal_event_stops_without_listening() -> None:
    source = Source([make_event(RUN, 1), make_event(RUN, 2, "run_failed", status="failed")])
    bus = ScriptedBus(then_idle=True)
    frames = await collect(stream(bus, source, last_seq=0))
    assert [e["seq"] for e in events_in(frames)] == [1, 2]
    assert bus.subscriptions[0].closed
    assert bus.subscriptions[0].timeouts == []  # never waited for live events


async def test_replay_stops_at_the_first_terminal_event() -> None:
    source = Source([make_event(RUN, 1, "run_completed"), make_event(RUN, 2)])
    frames = await collect(stream(ScriptedBus(), source))
    assert [e["seq"] for e in events_in(frames)] == [1]


# ---- run already finished
@pytest.mark.parametrize(
    ("state", "type_"),
    [
        ("succeeded", "run_completed"),
        ("failed", "run_failed"),
        ("budget_stopped", "budget_stopped"),
    ],
)
async def test_finished_run_with_no_stored_events_gets_one_synthetic_terminal_event(
    state: str, type_: str
) -> None:
    frames = await collect(stream(ScriptedBus(then_idle=True), Source([], state=state)))
    seen = events_in(frames)
    assert [(e["type"], e["seq"]) for e in seen] == [(type_, 1)]
    assert seen[0]["payload"]["status"] == state
    assert seen[0]["payload"]["synthesised"] is True


async def test_synthetic_terminal_event_continues_the_sequence() -> None:
    source = Source([make_event(RUN, 1), make_event(RUN, 2)], state="succeeded")
    frames = await collect(stream(ScriptedBus(then_idle=True), source))
    assert [(e["seq"], e["type"]) for e in events_in(frames)] == [
        (1, "agent_started"),
        (2, "agent_started"),
        (3, "run_completed"),
    ]


async def test_finished_run_resumed_after_its_last_event_closes_at_once() -> None:
    source = Source([make_event(RUN, 1), make_event(RUN, 2, "run_completed")], state="succeeded")
    bus = ScriptedBus(then_idle=True)
    frames = await collect(stream(bus, source, last_seq=2))
    # the closing event is re-sent as a synthetic one with the next sequence number, then it ends
    assert [(e["seq"], e["type"]) for e in events_in(frames)] == [(3, "run_completed")]
    assert bus.subscriptions[0].closed


async def test_no_synthetic_event_when_the_stored_terminal_event_was_already_sent() -> None:
    source = Source([make_event(RUN, 1), make_event(RUN, 2, "run_completed")], state="succeeded")
    frames = await collect(stream(ScriptedBus(then_idle=True), source, last_seq=0))
    assert [e["seq"] for e in events_in(frames)] == [1, 2]


@pytest.mark.parametrize(
    "state", ["running", "awaiting_input", "awaiting_approval", "created", None]
)
async def test_unfinished_or_unknown_state_keeps_listening(state: str | None) -> None:
    bus = ScriptedBus([wire(make_event(RUN, 1, "run_completed"))])
    frames = await collect(stream(bus, Source([], state=state)))
    assert [e["seq"] for e in events_in(frames)] == [1]
    assert bus.subscriptions[0].timeouts  # it did wait for live events


# ---- heartbeat
async def test_an_idle_connection_gets_a_heartbeat_without_an_id() -> None:
    bus = ScriptedBus([None, None, wire(make_event(RUN, 1, "run_completed"))])
    frames = await collect(stream(bus, Source(), heartbeat_s=15))
    beats = [f for f in frames if f.startswith("event: heartbeat")]
    assert len(beats) == 2
    assert all("\nid:" not in b for b in beats)  # Last-Event-ID must not move on a heartbeat
    data = json.loads(beats[0].split("data: ", 1)[1])
    assert (data["type"], data["run_id"], data["payload"]) == ("heartbeat", RUN, {})


async def test_the_wait_for_events_never_exceeds_the_heartbeat_interval() -> None:
    bus = ScriptedBus([None, wire(make_event(RUN, 1, "run_completed"))])
    await collect(stream(bus, Source(), heartbeat_s=15))
    assert all(0 <= t <= 15 for t in bus.subscriptions[0].timeouts)


async def test_heartbeat_carries_the_last_sequence_number_seen() -> None:
    bus = ScriptedBus([wire(make_event(RUN, 4)), None, wire(make_event(RUN, 5, "run_completed"))])
    frames = await collect(stream(bus, Source()))
    beat = next(f for f in frames if f.startswith("event: heartbeat"))
    assert json.loads(beat.split("data: ", 1)[1])["seq"] == 4


# ---- hostile or broken messages
async def test_garbage_and_foreign_messages_are_skipped() -> None:
    other = make_event("run_other", 2)
    bad_type = json.dumps(
        {"run_id": RUN, "seq": 2, "ts": "2026-01-01T00:00:00Z", "type": "nope", "payload": {}}
    )
    live = [
        "not json",
        "[]",
        "null",
        "",
        bad_type,
        json.dumps({"run_id": RUN, "seq": "x"}),
        wire(other),  # another run's event on this channel must never be forwarded
        json.dumps({**json.loads(wire(make_event(RUN, 2))), "extra": 1}),  # extra=forbid
        wire(make_event(RUN, 1, "run_completed")),
    ]
    frames = await collect(stream(ScriptedBus(live), Source()))
    assert [e["seq"] for e in events_in(frames)] == [1]


async def test_events_with_negative_or_zero_seq_are_ignored() -> None:
    live = [
        wire(make_event(RUN, 0)),
        wire(make_event(RUN, -3)),
        wire(make_event(RUN, 1, "run_completed")),
    ]
    frames = await collect(stream(ScriptedBus(live), Source()))
    assert [e["seq"] for e in events_in(frames)] == [1]


async def test_invalid_stored_events_are_skipped_not_fatal() -> None:
    source = Source(
        [make_event(RUN, 1), make_event("run_other", 2), make_event(RUN, 3, "run_completed")]
    )
    frames = await collect(stream(ScriptedBus(), source))
    assert [e["seq"] for e in events_in(frames)] == [1, 3]


# ---- bounded lifetime and disconnect
async def test_the_stream_ends_after_the_maximum_lifetime() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    class AgingBus(ScriptedBus):
        async def subscribe(self, run_id: str) -> Any:
            sub = await super().subscribe(run_id)
            original = sub.next

            async def next_(timeout_s: float) -> str | None:
                now[0] += 100
                return await original(timeout_s)

            sub.next = next_  # type: ignore[method-assign]
            return sub

    bus = AgingBus([None] * 50)
    frames = await collect(stream(bus, Source(), heartbeat_s=1, max_s=250, clock=clock))
    assert 1 <= len(frames) <= 4  # it stopped by itself, long before the script ran out
    assert bus.subscriptions[0].closed


async def test_closing_the_generator_closes_the_subscription() -> None:
    bus = ScriptedBus([wire(make_event(RUN, 1))], then_idle=True)
    gen = stream(bus, Source())
    first = await gen.__anext__()
    assert first.startswith("retry:") or "id: 1" in first
    await gen.aclose()  # what Starlette does when the client goes away
    assert bus.subscriptions[0].closed


async def test_cancelling_while_waiting_closes_the_subscription() -> None:
    bus = ScriptedBus(then_idle=True)
    gen = stream(bus, Source())

    async def drain() -> None:
        async for _ in gen:
            pass

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.subscriptions[0].closed


async def test_a_broken_bus_ends_the_stream_cleanly() -> None:
    class Broken(ScriptedBus):
        async def subscribe(self, run_id: str) -> Any:
            raise OSError("redis down")

    assert await collect(stream(Broken(), Source())) == []


def error_frames(frames: list[str]) -> list[str]:
    return [f for f in frames if f.startswith("event: error")]


async def test_a_failing_replay_ends_the_stream_with_an_error_event() -> None:
    class BadReplay(Source):
        async def replay(self, after: int) -> list[RunEvent]:
            raise RuntimeError("m3 down")

    bus = ScriptedBus(then_idle=True)
    frames = await collect(stream(bus, BadReplay()))
    assert frames[0].startswith("retry:")
    assert len(error_frames(frames)) == 1
    assert "m3 down" not in "".join(frames)
    assert bus.subscriptions[0].closed


async def test_a_failing_state_lookup_ends_the_stream_with_an_error_event() -> None:
    class BadState(Source):
        async def current_state(self) -> str | None:
            raise RuntimeError("down")

    bus = ScriptedBus(then_idle=True)
    frames = await collect(stream(bus, BadState()))
    assert len(error_frames(frames)) == 1
    assert bus.subscriptions[0].closed


async def test_a_redis_failure_while_waiting_ends_the_stream_with_an_error_event() -> None:
    class Flaky(ScriptedBus):
        async def subscribe(self, run_id: str) -> Any:
            sub = await super().subscribe(run_id)

            async def broken(timeout_s: float) -> str | None:
                raise OSError("redis connection lost")

            sub.next = broken  # type: ignore[method-assign]
            return sub

    bus = Flaky()
    frames = await collect(stream(bus, Source()))
    assert len(error_frames(frames)) == 1
    assert "redis" not in "".join(frames).lower().replace("retry", "")
    assert bus.subscriptions[0].closed


async def test_a_failing_gap_fill_ends_the_stream_with_an_error_event() -> None:
    class GapFails(Source):
        async def replay(self, after: int) -> list[RunEvent]:
            if self.replays:
                raise RuntimeError("m3 down")
            self.replays.append(after)
            return []

    bus = ScriptedBus([wire(make_event(RUN, 1)), wire(make_event(RUN, 3))])
    frames = await collect(stream(bus, GapFails()))
    assert [e["seq"] for e in events_in([f for f in frames if "event: error" not in f])] == [1]
    assert len(error_frames(frames)) == 1
