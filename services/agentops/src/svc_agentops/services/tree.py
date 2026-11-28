"""Order records and build the step tree plus totals (pure functions, docs/harness/12 §1).

A span is emitted when it ends, so a child is stored before its parent: order uses the estimated
start ``ts - latency``. ``step_id`` counts per service process, so a parent is looked up among the
records of the same service with that step id; if a restart reused the number, the nearest
later-ending one wins.
"""

from datetime import datetime, timedelta
from typing import Any

from common.schemas.trace import TraceRecord

from ..models.traces import RunTotals, Totals, TraceNode

Entry = tuple[str, TraceRecord]


def _start(r: TraceRecord) -> datetime:
    return r.ts - timedelta(milliseconds=r.latency_ms)


def ordered(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: (_start(e[1]), e[1].service or "", e[1].step_id, e[0]))


def _add(t: Totals, r: TraceRecord) -> None:
    t.steps += 1
    t.tokens_in += r.tokens_in
    t.tokens_out += r.tokens_out
    t.cost_usd = round(t.cost_usd + r.cost_usd, 8)
    t.latency_ms += r.latency_ms
    t.errors += 1 if r.error else 0


def totals(entries: list[Entry]) -> tuple[RunTotals, dict[str, Totals]]:
    run = RunTotals()
    by_agent: dict[str, Totals] = {}
    for _, r in entries:
        _add(run, r)
        _add(by_agent.setdefault(r.agent, Totals()), r)
    if entries:
        run.started_at = min(_start(r) for _, r in entries)
        run.ended_at = max(r.ts for _, r in entries)
        run.wall_ms = int((run.ended_at - run.started_at).total_seconds() * 1000)
    return run, by_agent


def build_tree(entries: list[Entry]) -> list[TraceNode]:
    items = ordered(entries)
    by_step: dict[tuple[str, int], list[Entry]] = {}
    for e in items:
        by_step.setdefault((e[1].service or "", e[1].step_id), []).append(e)
    nodes = {rid: TraceNode(record_id=rid, record=_dump(r)) for rid, r in items}
    roots: list[TraceNode] = []
    for rid, r in items:
        node = nodes[rid]
        parent = _find_parent(by_step, r)
        if parent is None:
            node.orphan = r.parent_step is not None
            roots.append(node)
        else:
            nodes[parent].children.append(node)
    return roots


def _find_parent(by_step: dict[tuple[str, int], list[Entry]], r: TraceRecord) -> str | None:
    if r.parent_step is None or r.parent_step >= r.step_id:
        return None  # a parent starts before its child, so a cycle cannot form
    candidates = [e for e in by_step.get((r.service or "", r.parent_step), [])]
    if not candidates:
        return None
    later = [e for e in candidates if e[1].ts >= r.ts and _start(e[1]) <= _start(r)]
    pool = later or candidates
    return min(pool, key=lambda e: abs((e[1].ts - r.ts).total_seconds()))[0]


def _dump(r: TraceRecord) -> dict[str, Any]:
    return r.model_dump(mode="json")
