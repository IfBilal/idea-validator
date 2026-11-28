"""Run labelled items through the real Validate flow of the gateway and collect one row each.

The verdict in a row is exactly what M11 returned in the card; it is never inferred, defaulted or
adjusted here. An item that cannot produce a verdict becomes an *error row* (``status="error"``
and a short ``error`` code): reported, never dropped, and never counted as INSUFFICIENT_EVIDENCE.

Safety rules: a deep-search approval is always declined (no extra spend); a clarification
question is answered only with the labelled profile's own value (or the item's
``estimated_effort_hours``); with no such value it is left unanswered.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evaluation.behaviour import run_checks, trace_stats
from evaluation.gateway import Gateway, GatewayError, NotFoundError, Sleep
from evaluation.labelled import LabelledItem
from evaluation.metrics import LABELS

TERMINAL = frozenset({"succeeded", "failed", "budget_stopped", "cancelled"})
PROFILE_ANSWERS = ("hours_per_week", "weeks_available", "team_size")

Row = dict[str, Any]


@dataclass(frozen=True)
class RunConfig:
    concurrency: int = 2
    item_timeout_s: float = 900.0
    poll_s: float = 2.0
    item_retries: int = 2
    max_cost_usd: float = 0.05
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
    """Log in; on a 401 register the user once and log in again."""
    creds = {"email": email, "password": password}
    try:
        data = await gateway.request("POST", "/auth/login", body=creds)
    except GatewayError as exc:
        if exc.status != 401:
            raise
        await gateway.request("POST", "/auth/register", body=creds)
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
            raise ItemError("malformed_run", retryable=False)
        state = view["state"]
        if state in TERMINAL:
            return view
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


async def _fetch_trace(trace_gateway: Gateway | None, run_id: str) -> list[dict[str, Any]] | None:
    if trace_gateway is None:
        return None
    try:
        data = await trace_gateway.request("GET", f"/runs/{run_id}/trace")
    except (NotFoundError, GatewayError):
        return None
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list) or not records:
        return None
    if not all(isinstance(r, dict) for r in records):
        return None
    return records


def _card(view: Mapping[str, Any]) -> dict[str, Any]:
    state = view["state"]
    if state == "budget_stopped":
        raise ItemError("budget_stopped")
    if state != "succeeded":
        raise ItemError(f"run_{state}", retryable=state == "failed")
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


async def _attempt(
    gateway: Gateway,
    trace_gateway: Gateway | None,
    session: Session,
    item: LabelledItem,
    cfg: RunConfig,
    sleep: Sleep,
    attempt: int,
) -> Row:
    await gateway.request("PUT", "/profile", token=session.token, body=item.profile_body())
    accepted = await gateway.request(
        "POST",
        "/validate",
        token=session.token,
        body={"idea_text": item.idea_text, "title": f"eval {item.id}"},
        headers={"Idempotency-Key": f"{cfg.run_tag}-{item.id}-{attempt}"[:64]},
    )
    run_id = accepted.get("run_id") if isinstance(accepted, dict) else None
    if not isinstance(run_id, str) or not run_id:
        raise ItemError("malformed_run")
    notes: list[str] = []
    try:
        async with asyncio.timeout(cfg.item_timeout_s):
            view = await _drive(gateway, session, item, run_id, cfg, sleep, notes)
    except TimeoutError:
        raise ItemError(
            "unanswered_question" if "unanswered_question" in notes else "timeout"
        ) from None
    card = _card(view)
    system = str(card["verdict"])
    trace = await _fetch_trace(trace_gateway, run_id)
    raw_spent, raw_budget = view.get("spent"), view.get("budget")
    spent: dict[str, Any] = raw_spent if isinstance(raw_spent, dict) else {}
    budget: dict[str, Any] = raw_budget if isinstance(raw_budget, dict) else {}
    checks = run_checks(item, card, system, spent, budget, trace, cfg.max_cost_usd)
    stats = trace_stats(trace)
    cost = spent.get("cost_usd") if isinstance(spent.get("cost_usd"), int | float) else None
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
    trace_gateway: Gateway | None,
    session: Session,
    item: LabelledItem,
    cfg: RunConfig,
    *,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Row:
    """One row for one item; never raises for an item-level problem."""
    base: Row = {
        "id": item.id,
        "human": item.human_verdict,
        "category": item.category,
        "poisoned": item.poisoned,
    }
    started = clock()
    code = "error"
    attempts = 0
    for attempt in range(cfg.item_retries + 1):
        attempts = attempt + 1
        try:
            row = await _attempt(gateway, trace_gateway, session, item, cfg, sleep, attempt)
        except ItemError as exc:
            code = exc.code
            if not exc.retryable:
                break
        except GatewayError as exc:
            code = "gateway_error" if exc.retryable else f"http_{exc.status}"
            if not exc.retryable:
                break
        else:
            return {
                **base,
                "status": "ok",
                "error": None,
                "attempts": attempts,
                "latency_s": round(clock() - started, 3),
                **row,
            }
    return {
        **base,
        "status": "error",
        "error": code,
        "attempts": attempts,
        "latency_s": round(clock() - started, 3),
        "system": None,
        "run_id": None,
        "cost_usd": None,
    }


async def run_all(
    items: Sequence[LabelledItem],
    sessions: Sequence[Session],
    gateway: Gateway,
    trace_gateway: Gateway | None,
    cfg: RunConfig,
    *,
    on_row: Callable[[Row], Awaitable[None] | None] | None = None,
    sleep: Sleep = asyncio.sleep,
) -> list[Row]:
    """Run all items on ``len(sessions)`` workers; the rows come back in input order."""
    queue: asyncio.Queue[LabelledItem] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)
    rows: dict[str, Row] = {}

    async def worker(session: Session) -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            row = await run_item(gateway, trace_gateway, session, item, cfg, sleep=sleep)
            rows[item.id] = row
            if on_row is not None:
                result = on_row(row)
                if result is not None:
                    await result

    slots = list(sessions)[: max(1, cfg.concurrency)]
    await asyncio.gather(*(worker(s) for s in slots))
    return [rows[item.id] for item in items if item.id in rows]
