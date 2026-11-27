"""Behaviour: a module times out (docs/harness/07 section 9: tool timeout, retry, then abstain).

Every outbound call is bounded: at most 1 + max_retries attempts, then the run degrades (an
optional step) or fails with a typed error naming the step (a required step). It never hangs
and it never invents a verdict.
"""

import asyncio
from typing import Any

import pytest

from common.config import ConfigError
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import FakeWorld, Harness

SEARCH = ("kb", "POST /search")
FEATURES = ("analyst", "POST /features")
SCOUT = ("scouts", "POST /scout")
VERDICT = ("verdict", "POST /verdict")
CHECK = ("critic", "POST /check")
ATTEMPTS = 3  # 1 try + the default 2 retries
EFFORT = {"estimated_effort_hours": 50}


async def run(h: Harness) -> ValidateState:
    async with asyncio.timeout(20):  # a hang would fail here instead of stalling the suite
        await h.start(overrides=EFFORT)
    return await h.state()


def thin_world() -> FakeWorld:
    world = FakeWorld()
    world.evidence = world.evidence[:1]  # thin evidence makes the run scout
    return world


@pytest.mark.parametrize("mode", ["fault", "hang"])
async def test_an_optional_module_that_times_out_degrades_the_run_with_bounded_calls(
    mode: str,
) -> None:
    world = FakeWorld()
    if mode == "hang":
        world.hang.add(FEATURES)
    else:
        world.faults[FEATURES] = ["timeout"] * 50
    h = Harness(world)
    state = await run(h)
    assert world.count(*FEATURES) == ATTEMPTS
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is True
    assert state.features == {}
    assert state.card is not None
    assert state.card["degraded"] is True
    assert state.card["verdict"] == "GO"  # M11 still answered from what was available
    assert world.cache == {}
    assert "run_completed" in h.event_types()
    record = next(r for r in h.records if r.tool == "m7.features")
    assert record.error is not None
    assert record.retries == 2


async def test_a_scout_that_times_out_is_bounded_and_the_run_continues_without_it() -> None:
    world = thin_world()
    world.faults[SCOUT] = ["timeout"] * 50
    state = await run(Harness(world))
    assert world.count(*SCOUT) <= 3 * ATTEMPTS  # three sources, each bounded
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is True
    assert world.count("validators", "POST /score-all") == 1
    assert world.cache == {}


async def test_when_every_search_times_out_the_run_fails_with_a_typed_error() -> None:
    world = FakeWorld()
    world.hang.add(SEARCH)
    h = Harness(world)
    state = await run(h)
    assert world.count(*SEARCH) == 4 * ATTEMPTS  # four angles, each bounded
    assert state.status is RunStatus.FAILED
    assert state.error is not None and "EVIDENCE" in state.error
    assert world.count("validators", "POST /score-all") == 0  # nothing paid after the failure
    assert world.count(*VERDICT) == 0
    assert state.card is not None
    assert state.card["partial"] is True and state.card["verdict"] is None
    assert "run_failed" in h.event_types()
    assert "run_completed" not in h.event_types()


async def test_when_m11_times_out_the_run_fails_and_no_verdict_is_invented() -> None:
    world = FakeWorld()
    world.hang.add(VERDICT)
    state = await run(Harness(world))
    assert world.count(*VERDICT) == ATTEMPTS
    assert state.status is RunStatus.FAILED
    assert state.error is not None and "VERDICT" in state.error
    assert state.verdict is None
    assert state.card is not None and state.card["verdict"] is None
    assert world.cache == {}


async def test_when_the_critic_times_out_the_guardrail_is_not_skipped() -> None:
    world = FakeWorld()
    world.hang.add(CHECK)
    state = await run(Harness(world))
    assert world.count(*CHECK) == ATTEMPTS
    assert state.status is RunStatus.FAILED
    assert state.card is not None and state.card["verdict"] is None  # unchecked = not final
    assert world.cache == {}


async def test_a_timeout_then_recovery_is_invisible_to_the_card() -> None:
    world = FakeWorld()
    world.faults[SEARCH] = ["timeout", "timeout"]  # the first two search attempts only
    state = await run(Harness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.degraded is False
    assert world.count(*SEARCH) == 4 + 2  # four angles plus the two retried attempts


@pytest.mark.parametrize("max_retries", [0, 1, 2])
async def test_total_calls_follow_the_configured_retry_bound(max_retries: int) -> None:
    world = FakeWorld()
    world.hang.add(FEATURES)
    await run(Harness(world, max_retries=max_retries))
    assert world.count(*FEATURES) == 1 + max_retries


async def test_more_retries_than_the_hard_cap_is_a_startup_error() -> None:
    with pytest.raises(ConfigError):
        Harness(max_retries=3)


async def test_a_slow_module_never_makes_the_run_exceed_its_step_budget() -> None:
    world = FakeWorld()
    world.hang.add(SEARCH)
    h = Harness(world)
    await run(h)
    spent: Any = await h.ledger.spent()
    assert spent.steps <= h.config.limits.max_steps  # retries are not extra steps
