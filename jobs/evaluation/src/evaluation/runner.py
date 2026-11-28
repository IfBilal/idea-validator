"""Run labelled items through the real Validate flow of the gateway and collect one row each.

The verdict in a row is exactly what M11 returned in the card; it is never inferred, defaulted or
adjusted here. An item that cannot produce a verdict becomes an *error row* (``status="error"``
and a short ``error`` code): reported, never dropped, and never counted as INSUFFICIENT_EVIDENCE.

Safety rules: a deep-search approval is always declined (no extra spend); a clarification
question is answered only with the labelled profile's own value (or the item's
``estimated_effort_hours``); with no such value it is left unanswered.
"""

import asyncio
import contextlib
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evaluation.behaviour import finite_number, run_checks, trace_stats
from evaluation.gateway import MAX_TRACE_BYTES, Gateway, GatewayError, NotFoundError, Sleep
from evaluation.labelled import LabelledItem
from evaluation.metrics import LABELS

RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
COST_EPS = 1e-9
TERMINAL = frozenset({"succeeded", "failed", "budget_stopped", "cancelled"})
PROFILE_ANSWERS = ("hours_per_week", "weeks_available", "team_size")

Row = dict[str, Any]


@dataclass(frozen=True)
class RunConfig:
    concurrency: int = 4
    item_timeout_s: float = 900.0
    poll_s: float = 4.0
    item_retries: int = 2
    max_cost_usd: float = 0.05
    trace_tries: int = 3
    trace_wait_s: float = 1.0
    run_tag: str = "eval"


class ItemError(Exception):
    def __init__(self, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass
class Session:
    """One eval user: the profile is per user, so a slot never shares its user with another."""

    token: str
    email: str


async def sign_in(gateway: Gateway, email: str, password: str) -> Session:
    """Log in. On a 401 register the user once and log in again.

    The gateway answers 401 for an unknown user and for a wrong password alike, so a 401 is
    followed by a registration attempt: for an account that already exists that is refused with
    409, which is reported as a rejected password (the account is never taken over or reset).
    """
    creds = {"email": email, "password": password}
    try:
        data = await gateway.request("POST", "/auth/login", body=creds)
    except GatewayError as exc:
        if exc.status != 401:
            raise
        try:
            await gateway.request("POST", "/auth/register", body=creds)
        except GatewayError as reg:
            if reg.status == 409:
                raise GatewayError(
                    "the eval user exists but its password was rejected (EVAL_PASSWORD changed?)",
                    401,
                ) from None
            raise
        data = await gateway.request("POST", "/auth/login", body=creds)
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise GatewayError("login returned no access token")
    return Session(token=token, email=email)


def slot_email(base: str, slot: int) -> str:
    local, _, domain = base.partition("@")
    return f"{local}+w{slot}@{domain}"


def _answer_for(question: Mapping[str, Any], item: LabelledItem) -> float | None:
    field = question.get("field")
    if field == "estimated_effort_hours":
        return item.estimated_effort_hours
    if field in PROFILE_ANSWERS:
        value = item.profile.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return None


async def _drive(
    gateway: Gateway,
    session: Session,
    item: LabelledItem,
    run_id: str,
    cfg: RunConfig,
    sleep: Sleep,
    notes: list[str],
) -> dict[str, Any]:
    """Poll the run to a terminal state, answering/declining as the rules above allow."""
    answered: set[str] = set()
    while True:
        view = await gateway.request("GET", f"/runs/{run_id}", token=session.token)
        if not isinstance(view, dict) or not isinstance(view.get("state"), str):
            raise ItemError("malformed_run")
        state = view["state"]
        if state in TERMINAL:
            return view
        _enforce_budget(view, cfg)
        approval = view.get("pending_approval")
        if state == "awaiting_approval" and isinstance(approval, dict):
            key = f"approval:{approval.get('approval_id')}"
            if key not in answered and isinstance(approval.get("approval_id"), str):
                answered.add(key)
                notes.append("approval_declined")
                await gateway.request(
                    "POST",
                    f"/runs/{run_id}/approve",
                    token=session.token,
                    body={"approval_id": approval["approval_id"], "decision": "decline"},
                )
        question = view.get("pending_question")
        if state == "awaiting_input" and isinstance(question, dict):
            qid = question.get("question_id")
            if isinstance(qid, str) and qid not in answered:
                value = _answer_for(question, item)
                if value is None:
                    notes.append("unanswered_question")
                else:
                    answered.add(qid)
                    await gateway.request(
                        "POST",
                        f"/runs/{run_id}/answer",
                        token=session.token,
                        body={"question_id": qid, "answer": value},
                    )
        await sleep(cfg.poll_s)


def _code(exc: GatewayError) -> str:
    """A short error code for a row; never the exception text (it can quote the idea)."""
    if exc.retryable:
        return "gateway_error"
    if "too large" in str(exc):
        return "response_too_large"
    return f"http_{exc.status}" if exc.status is not None else "bad_response"


def _enforce_budget(view: Mapping[str, Any], cfg: RunConfig) -> None:
    """Preflight and in-flight guard on a run that is still going: no cost limit means no run."""
    budget, spent = view.get("budget"), view.get("spent")
    limit = finite_number(budget.get("max_cost_usd")) if isinstance(budget, dict) else None
    if limit is None or limit <= 0:
        raise ItemError("no_budget_limit")
    used = finite_number(spent.get("cost_usd")) if isinstance(spent, dict) else None
    if used is not None and used > min(limit, cfg.max_cost_usd) + COST_EPS:
        raise ItemError("budget_exceeded")


async def _cancel(gateway: Gateway, session: Session, run_id: str) -> None:
    """Best effort: ask the gateway to stop a run we no longer watch. Never raises."""
    with contextlib.suppress(Exception):
        await gateway.request("POST", f"/runs/{run_id}/cancel", token=session.token)


async def _fetch_trace(
    gateway: Gateway, session: Session, run_id: str, cfg: RunConfig, sleep: Sleep
) -> list[dict[str, Any]] | None:
    """The run's flat trace through the gateway with the eval user's own token.

    Records reach M13 in batches a moment after the run ends, so an empty or 404 answer is
    retried ``cfg.trace_tries`` times. ``None`` means no usable trace (the checks then fail).
    """
    for attempt in range(cfg.trace_tries):
        try:
            data = await gateway.request(
                "GET",
                f"/runs/{run_id}/trace?flat=true",
                token=session.token,
                max_bytes=MAX_TRACE_BYTES,
            )
        except (NotFoundError, GatewayError):
            data = None
        records = data.get("records") if isinstance(data, dict) else data
        if (
            isinstance(records, list)
            and records
            and all(isinstance(r, dict) for r in records)
            and not (isinstance(data, dict) and data.get("run_id") not in (None, run_id))
        ):
            return records
        if attempt + 1 < cfg.trace_tries:
            await sleep(cfg.trace_wait_s)
    return None


def _card(view: Mapping[str, Any]) -> dict[str, Any]:
    state = view["state"]
    if state == "budget_stopped":
        raise ItemError("budget_stopped")
    if state != "succeeded":
        raise ItemError(f"run_{state}")
    card = view.get("result")
    if not isinstance(card, dict):
        raise ItemError("malformed_card")
    if card.get("verdict") is None:
        raise ItemError("no_verdict")  # partial card: M11 gave no final verdict
    if card["verdict"] not in LABELS or card.get("stability") not in ("STABLE", "BORDERLINE"):
        raise ItemError("malformed_card")
    for key in ("score", "confidence"):
        value = card.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ItemError("malformed_card")
    return card


async def _start(gateway: Gateway, session: Session, item: LabelledItem, cfg: RunConfig) -> str:
    """Set the profile and start the run. The idempotency key is the same on every attempt, so a
    start whose answer was lost is replayed by the gateway instead of creating a second run."""
    await gateway.request("PUT", "/profile", token=session.token, body=item.profile_body())
    accepted = await gateway.request(
        "POST",
        "/validate",
        token=session.token,
        body={"idea_text": item.idea_text, "title": f"eval {item.id}"},
        headers={"Idempotency-Key": f"{cfg.run_tag}-{item.id}"[:64]},
    )
    run_id = accepted.get("run_id") if isinstance(accepted, dict) else None
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ItemError("malformed_run")
    return run_id


async def _finish(
    gateway: Gateway,
    session: Session,
    item: LabelledItem,
    run_id: str,
    cfg: RunConfig,
    sleep: Sleep,
) -> Row:
    """Everything after the run exists. Failures here are never retried (that would start a
    second run for the same item); a run still going when we give up is cancelled."""
    notes: list[str] = []
    finished = False
    try:
        try:
            async with asyncio.timeout(cfg.item_timeout_s):
                view = await _drive(gateway, session, item, run_id, cfg, sleep, notes)
        except TimeoutError:
            raise ItemError(
                "unanswered_question" if "unanswered_question" in notes else "timeout"
            ) from None
        finished = True
        card = _card(view)
        system = str(card["verdict"])
        trace = await _fetch_trace(gateway, session, run_id, cfg, sleep)
    except GatewayError as exc:
        raise ItemError(_code(exc)) from None
    finally:
        if not finished:
            await _cancel(gateway, session, run_id)
    raw_spent, raw_budget = view.get("spent"), view.get("budget")
    spent: dict[str, Any] = raw_spent if isinstance(raw_spent, dict) else {}
    budget: dict[str, Any] = raw_budget if isinstance(raw_budget, dict) else {}
    checks = run_checks(item, card, system, spent, budget, trace, cfg.max_cost_usd)
    stats = trace_stats(trace)
    cost = finite_number(spent.get("cost_usd"))
    return {
        "system": system,
        "stability": card["stability"],
        "score": card["score"],
        "confidence": card["confidence"],
        "run_id": run_id,
        "cost_usd": cost if cost is not None else stats["trace_cost_usd"],
        "cache_hit": bool(card.get("cache_hit", False)),
        "fallbacks": stats["fallbacks"],
        "agent_latency_ms": stats["agent_latency_ms"],
        "trace_available": trace is not None,
        "notes": sorted(set(notes)),
        "behaviour": checks,
    }


async def run_item(
    gateway: Gateway,
    session: Session,
    item: LabelledItem,
    cfg: RunConfig,
    *,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Row:
    """One row for one item; never raises for an item-level problem (not even a hostile reply)."""
    base: Row = {
        "id": item.id,
        "human": item.human_verdict,
        "category": item.category,
        "poisoned": item.poisoned,
    }
    started = clock()
    code = "error"
    attempts = 0
    run_id: str | None = None
    row: Row | None = None
    for attempt in range(cfg.item_retries + 1):
        attempts = attempt + 1
        try:
            run_id = await _start(gateway, session, item, cfg)
            row = await _finish(gateway, session, item, run_id, cfg, sleep)
            break
        except ItemError as exc:
            code = exc.code
            break
        except GatewayError as exc:  # only _start can raise this: no run exists yet
            code = _code(exc)
            if not exc.retryable:
                break
        except Exception as exc:  # a reply of an unexpected shape; its text can quote the idea
            code = f"unexpected_{type(exc).__name__}"
            break
    elapsed = round(clock() - started, 3)
    if row is not None:
        return {
            **base,
            "status": "ok",
            "error": None,
            "attempts": attempts,
            "latency_s": elapsed,
            **row,
        }
    return {
        **base,
        "status": "error",
        "error": code,
        "attempts": attempts,
        "latency_s": elapsed,
        "system": None,
        "run_id": run_id,
        "cost_usd": None,
    }


async def run_all(
    items: Sequence[LabelledItem],
    sessions: Sequence[Session],
    gateway: Gateway,
    cfg: RunConfig,
    *,
    on_row: Callable[[Row], Awaitable[None] | None] | None = None,
    sleep: Sleep = asyncio.sleep,
    max_total_cost_usd: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[Row]:
    """Run all items on ``len(sessions)`` workers; the rows come back in input order.

    When the summed cost of finished items reaches ``max_total_cost_usd`` no new item is started;
    every item not started becomes an error row ``total_budget_stop`` (reported, not dropped).
    """
    queue: asyncio.Queue[LabelledItem] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)
    rows: dict[str, Row] = {}
    total = 0.0

    async def emit(row: Row) -> None:
        rows[row["id"]] = row
        if on_row is not None:
            result = on_row(row)
            if result is not None:
                await result

    async def worker(session: Session) -> None:
        nonlocal total
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if max_total_cost_usd is not None and total >= max_total_cost_usd:
                await emit(_skipped(item))
                continue
            row = await run_item(gateway, session, item, cfg, sleep=sleep, clock=clock)
            total += finite_number(row.get("cost_usd")) or 0.0
            await emit(row)

    slots = list(sessions)[: max(1, cfg.concurrency)]
    await asyncio.gather(*(worker(s) for s in slots))
    return [rows[item.id] for item in items if item.id in rows]


def _skipped(item: LabelledItem) -> Row:
    return {
        "id": item.id,
        "human": item.human_verdict,
        "category": item.category,
        "poisoned": item.poisoned,
        "status": "error",
        "error": "total_budget_stop",
        "attempts": 0,
        "latency_s": 0.0,
        "system": None,
        "run_id": None,
        "cost_usd": None,
    }
