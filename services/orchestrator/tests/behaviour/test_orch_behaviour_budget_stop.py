"""Behaviour: the budget runs out in the middle of a run (CLAUDE.md rule 8).

Status budget_stopped, a partial card (never a verdict M3 made up), the event budget_stopped as
the last event, and no module call after the stop. Limits are probed at N-1, N and N+1.
"""

from typing import Any

import pytest

from common.budget import Spend
from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import DiscoverHarness, FakeWorld, Harness

VERDICT = ("verdict", "POST /verdict")
SCORE_ALL = ("validators", "POST /score-all")
EFFORT = {"estimated_effort_hours": 50}


async def run(h: Harness) -> ValidateState:
    await h.start(overrides=EFFORT)
    return await h.state()


async def steps_of_a_clean_run() -> int:
    h = Harness()
    await run(h)
    steps: int = (await h.ledger.spent()).steps
    return steps


def calls_after_the_stop(records: list[Any]) -> list[str]:
    """Tool calls traced after the budget_stop decision (there must be none)."""
    stop = next(
        i
        for i, r in enumerate(records)
        if r.kind == "decision" and r.output.get("decision") == "budget_stop"
    )
    return [r.tool for r in records[stop + 1 :] if r.kind == "tool_call"]


async def test_the_clean_run_needs_a_sane_number_of_steps() -> None:
    assert await steps_of_a_clean_run() >= 10


@pytest.mark.parametrize(("delta", "stopped"), [(1, False), (0, False), (-1, True), (-2, True)])
async def test_step_limit_boundaries_n_plus_one_n_n_minus_one(delta: int, stopped: bool) -> None:
    n = await steps_of_a_clean_run()
    h = Harness(limits={"max_steps": n + delta})
    state = await run(h)
    assert (state.status is RunStatus.BUDGET_STOPPED) is stopped
    assert (await h.ledger.spent()).steps <= n + delta  # never overshoots the limit
    assert state.card is not None
    assert (state.card["verdict"] is None) is stopped


@pytest.mark.parametrize("cut", [2, 4, 8, 12])
async def test_a_stopped_run_ends_with_a_partial_card_the_event_and_no_further_calls(
    cut: int,
) -> None:
    n = await steps_of_a_clean_run()
    h = Harness(limits={"max_steps": max(1, n - cut)})
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert state.stop_reason
    assert state.card is not None
    assert state.card["partial"] is True
    assert state.card["verdict"] is None  # nothing a model or M3 decided
    assert state.card["stop_reason"] == state.stop_reason
    types = h.event_types()
    assert types[-1] == "budget_stopped"
    assert "run_completed" not in types and "verdict" not in types
    assert calls_after_the_stop(h.records) == []
    assert h.world.cache == {}  # a partial card is never cached


async def test_the_stopped_run_makes_fewer_module_calls_than_a_full_one() -> None:
    n = await steps_of_a_clean_run()
    full = Harness()
    await run(full)
    stopped = Harness(limits={"max_steps": n - 6})
    await run(stopped)
    assert 0 < len(stopped.world.calls) < len(full.world.calls)
    assert stopped.world.count(*VERDICT) == 0


async def test_cost_exhausted_by_the_scorers_stops_before_the_verdict_call() -> None:
    world = FakeWorld()
    h = Harness(world)

    def spendy(body: dict[str, Any]) -> Any:
        # M5 mirrors the hosted-model cost of the scorers into the shared budget hash
        h.redis.hashes.setdefault("budget:run_1", {})["cost_usd"] = 0.07
        return {"scores": world._scores(body), "extra": world.extra}

    world.score_fn = spendy
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert world.count(*SCORE_ALL) == 1
    assert world.count(*VERDICT) == 0
    assert state.card is not None and len(state.card["scores"]) == 6  # what was computed is kept
    assert state.card["verdict"] is None
    assert h.event_types()[-1] == "budget_stopped"
    assert calls_after_the_stop(h.records) == []


@pytest.mark.parametrize(("spent", "stopped"), [(0.049, False), (0.05, False), (0.051, True)])
async def test_cost_cap_boundaries(spent: float, stopped: bool) -> None:
    h = Harness()
    await h.ledger.record(Spend(cost_usd=spent))
    state = await run(h)
    assert (state.status is RunStatus.BUDGET_STOPPED) is stopped


async def test_a_run_that_starts_exhausted_makes_no_call_at_all() -> None:
    h = Harness()
    await h.ledger.record(Spend(steps=10_000))
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert h.world.calls == []
    assert state.card is not None and state.card["verdict"] is None
    assert h.event_types()[-1] == "budget_stopped"


# ---- Discover: the debate and the children share one budget -----------------------------------
async def discover(h: DiscoverHarness) -> DiscoverState:
    return await h.start(count=3)


@pytest.mark.parametrize("max_steps", [3, 9, 15, 20])
async def test_discover_stops_with_the_partial_result_and_no_further_calls(max_steps: int) -> None:
    h = DiscoverHarness(FakeWorld(), limits={"max_steps": max_steps})
    state = await discover(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert state.card is not None
    assert state.card["partial"] is True
    assert state.card["stop_reason"]
    assert all(i["verdict"] is None for i in state.card["ideas"] if i["partial"])
    assert h.event_types()[-1] == "budget_stopped"
    assert "run_completed" not in h.event_types()
    assert calls_after_the_stop(h.records) == []


async def test_discover_limit_is_respected_exactly_at_the_boundary() -> None:
    probe = DiscoverHarness(FakeWorld(), limits={"max_steps": 100_000})
    done = await discover(probe)
    assert done.status is RunStatus.SUCCEEDED
    n = (await probe.ledger.spent()).steps
    enough = DiscoverHarness(FakeWorld(), limits={"max_steps": n})
    assert (await discover(enough)).status is RunStatus.SUCCEEDED
    short = DiscoverHarness(FakeWorld(), limits={"max_steps": n - 3})
    assert (await discover(short)).status is RunStatus.BUDGET_STOPPED
