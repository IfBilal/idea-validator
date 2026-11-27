"""The SSE stream of one run: stored events first, then live ones, heartbeats in between.

Rules (docs/harness/05 section 2):
- ``id:`` is the event ``seq``; a client resumes with ``Last-Event-ID`` and gets only newer events.
- Every event is validated as a ``RunEvent`` of *this* run before it is forwarded; anything else on
  the channel is dropped. Frames are rebuilt from the validated model, never passed through.
- The stream ends after ``run_completed``, ``run_failed`` or ``budget_stopped``, after
  ``max_s`` seconds (the client reconnects with ``Last-Event-ID``), or when the consumer closes the
  generator (client disconnect). The subscription is always closed.
- A heartbeat carries no ``id:`` so it never moves ``Last-Event-ID``.
"""

import json
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from common.logging import get_logger
from common.schemas import RunEvent
from common.schemas.trace import RunEventType

from ..clients.events import EventBus
from ..models.runs import TERMINAL_STATES

log = get_logger("gateway.stream")

TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "budget_stopped"})
_STATE_EVENT: dict[str, RunEventType] = {
    "succeeded": "run_completed",
    "failed": "run_failed",
    "budget_stopped": "budget_stopped",
}
RETRY_MS = 3000
DEFAULT_MAX_FRAME = 64 * 1024
RAW_FACTOR = 16  # a pub/sub message larger than this many frames is dropped unparsed
_FAILURE_REASONS = {
    "run_failed": "the run failed",
    "budget_stopped": "the run stopped at its budget",
}
_SEQ = re.compile(r"[0-9]{1,15}", re.ASCII)

Replay = Callable[[int], Awaitable[list[RunEvent]]]
StateOf = Callable[[], Awaitable[str | None]]


def parse_last_event_id(raw: str | None) -> int:
    """A missing, negative or malformed header means "from the start"."""
    value = (raw or "").strip()
    return int(value) if _SEQ.fullmatch(value) else 0


def stream_deadline(token_exp: float, now: float, max_s: float) -> float:
    """Seconds a stream may live: never longer than the cap or than the token it was opened with."""
    return max(0.0, min(max_s, token_exp - now))


def scrub_event(event: RunEvent) -> RunEvent:
    """Nothing M3 wrote as free text reaches a browser: failures get a fixed reason."""
    payload = event.payload
    if event.type in _FAILURE_REASONS:
        clean: dict[str, object] = {}
        status = payload.get("status")
        if isinstance(status, str) and len(status) <= 40 and status.replace("_", "").isalnum():
            clean["status"] = status
        if payload.get("synthesised") is True:
            clean["synthesised"] = True
        clean["reason"] = _FAILURE_REASONS[event.type]
        payload = clean
    elif isinstance(payload.get("error"), str):
        payload = {**payload, "error": "the step failed"}
    return event.model_copy(update={"payload": payload})


def _frame(event: RunEvent) -> str:
    return f"event: {event.type}\nid: {event.seq}\ndata: {event.model_dump_json()}\n\n"


def format_event(event: RunEvent, max_bytes: int = DEFAULT_MAX_FRAME) -> str:
    """One SSE frame. An event over ``max_bytes`` keeps its type and seq but loses its payload."""
    event = scrub_event(event)
    frame = _frame(event)
    if len(frame.encode()) <= max_bytes:
        return frame
    small = event.model_copy(update={"payload": {"truncated": True, "reason": "event too large"}})
    return _frame(small)


def _heartbeat(run_id: str, seq: int) -> str:
    event = RunEvent(run_id=run_id, seq=seq, ts=datetime.now(UTC), type="heartbeat", payload={})
    return f"event: heartbeat\ndata: {event.model_dump_json()}\n\n"


def _decode(raw: str, run_id: str, max_raw: int) -> RunEvent | None:
    """A pub/sub message as a RunEvent of this run, or ``None`` if it is anything else."""
    if len(raw) > max_raw:
        log.warning("event_dropped", run_id=run_id, reason="too_large")
        return None
    try:
        event = RunEvent.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        log.warning("event_dropped", run_id=run_id, reason="invalid")
        return None
    if event.run_id != run_id or event.seq < 1:
        log.warning("event_dropped", run_id=run_id, reason="foreign_or_bad_seq")
        return None
    return event


async def _stored(replay: Replay, run_id: str, after: int) -> list[RunEvent]:
    try:
        events = await replay(after)
    except Exception as exc:  # stored events are a convenience: live events still flow
        log.warning("replay_failed", run_id=run_id, error=type(exc).__name__)
        return []
    return sorted((e for e in events if e.run_id == run_id and e.seq > after), key=lambda e: e.seq)


async def _state(state: StateOf, run_id: str) -> str | None:
    try:
        return await state()
    except Exception as exc:
        log.warning("state_lookup_failed", run_id=run_id, error=type(exc).__name__)
        return None


async def stream_run_events(
    *,
    run_id: str,
    last_seq: int,
    bus: EventBus,
    replay: Replay,
    state: StateOf,
    heartbeat_s: float,
    max_s: float,
    max_frame_bytes: int = DEFAULT_MAX_FRAME,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[str]:
    try:
        subscription = await bus.subscribe(run_id)  # first, so nothing published later is lost
    except Exception as exc:
        log.warning("subscribe_failed", run_id=run_id, error=type(exc).__name__)
        return
    started = clock()
    sent = last_seq
    try:
        yield f"retry: {RETRY_MS}\n\n"

        for event in await _stored(replay, run_id, sent):
            yield format_event(event, max_frame_bytes)
            sent = event.seq
            if event.type in TERMINAL_EVENTS:
                return

        current = await _state(state, run_id)
        if current in TERMINAL_STATES:
            # M3 may have stored the closing event a moment ago: look once more, then synthesise
            for event in await _stored(replay, run_id, sent):
                yield format_event(event, max_frame_bytes)
                sent = event.seq
                if event.type in TERMINAL_EVENTS:
                    return
            closing = RunEvent(
                run_id=run_id,
                seq=sent + 1,
                ts=datetime.now(UTC),
                type=_STATE_EVENT[current],
                payload={"status": current, "synthesised": True},
            )
            yield format_event(closing, max_frame_bytes)
            return

        next_beat = clock() + heartbeat_s
        while clock() - started < max_s:
            message = await subscription.next(max(0.0, next_beat - clock()))
            if message is None:
                yield _heartbeat(run_id, sent)
                next_beat = clock() + heartbeat_s
                continue
            live = _decode(message, run_id, max_frame_bytes * RAW_FACTOR)
            if live is None or live.seq <= sent:
                continue
            if live.seq > sent + 1:  # pub/sub dropped something: fill the gap from storage
                for missed in await _stored(replay, run_id, sent):
                    if missed.seq >= live.seq:
                        break
                    yield format_event(missed, max_frame_bytes)
                    sent = missed.seq
                    if missed.type in TERMINAL_EVENTS:
                        return
            yield format_event(live, max_frame_bytes)
            sent = live.seq
            if live.type in TERMINAL_EVENTS:
                return
    finally:
        await subscription.aclose()
