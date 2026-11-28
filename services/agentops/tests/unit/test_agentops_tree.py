from datetime import timedelta
from typing import Any

from common.schemas.trace import TraceRecord
from svc_agentops.services.tree import build_tree, ordered, totals
from svc_agentops.testing import T0, rec


def at(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def entry(step: int, **over: Any) -> tuple[str, TraceRecord]:
    return (f"id{step}{over.get('service', '')}", TraceRecord.model_validate(rec(step, **over)))


def test_children_nest_under_parents_even_though_they_are_emitted_first() -> None:
    parent = entry(1, ts=at(10), latency_ms=9000)  # spans end after their children
    child = entry(2, parent_step=1, ts=at(5), latency_ms=1000)
    (root,) = build_tree([child, parent])
    assert root.record["step_id"] == 1
    assert [c.record["step_id"] for c in root.children] == [2]


def test_siblings_are_in_start_order_and_out_of_order_arrival_is_fixed() -> None:
    p = entry(1, ts=at(30), latency_ms=30000)
    c2 = entry(3, parent_step=1, ts=at(20), latency_ms=1000)
    c1 = entry(2, parent_step=1, ts=at(10), latency_ms=1000)
    (root,) = build_tree([c2, p, c1])
    assert [c.record["step_id"] for c in root.children] == [2, 3]
    assert [e[1].step_id for e in ordered([c2, p, c1])] == [1, 2, 3]


def test_a_missing_parent_makes_an_orphan_root_not_a_crash() -> None:
    roots = build_tree([entry(5, parent_step=3)])
    assert roots[0].orphan is True


def test_a_parent_step_that_is_not_smaller_cannot_form_a_cycle() -> None:
    roots = build_tree([entry(1, parent_step=2), entry(2, parent_step=1)])
    assert sum(1 for _ in roots) >= 1
    assert {r.record["step_id"] for r in roots} >= {1}


def test_services_have_their_own_step_numbering() -> None:
    m3 = entry(1, service="orchestrator", ts=at(9))
    m5 = entry(1, service="tools", agent="tools", ts=at(3))
    m5_child = entry(2, service="tools", agent="tools", parent_step=1)
    roots = build_tree([m3, m5, m5_child])
    tools_root = next(r for r in roots if r.record["service"] == "tools")
    assert len(tools_root.children) == 1
    assert next(r for r in roots if r.record["service"] == "orchestrator").children == []


def test_totals_per_agent_and_overall() -> None:
    entries = [
        entry(1, agent="a", tokens_in=1, tokens_out=2, cost_usd=0.1, latency_ms=10),
        entry(2, agent="a", tokens_in=3, tokens_out=4, cost_usd=0.2, latency_ms=20, error="boom"),
        entry(3, agent="b", tokens_in=5, tokens_out=6, cost_usd=0.3, latency_ms=30),
    ]
    run, by_agent = totals(entries)
    assert (run.steps, run.tokens_in, run.tokens_out, run.errors) == (3, 9, 12, 1)
    assert run.cost_usd == 0.6
    assert run.latency_ms == 60
    assert by_agent["a"].steps == 2
    assert by_agent["a"].errors == 1
    assert by_agent["b"].cost_usd == 0.3
    assert run.wall_ms > 0


def test_empty_totals_are_zero() -> None:
    run, by_agent = totals([])
    assert run.steps == 0
    assert by_agent == {}
