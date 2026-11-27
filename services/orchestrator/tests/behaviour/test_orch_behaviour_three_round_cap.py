"""Behaviour: the debate never runs more than 3 rounds (CLAUDE.md rule 9).

Objections that keep coming cannot extend it: not by configuration (a value above 3 is a startup
error) and not by a forged in-memory config either (the loop clamps to the hard cap itself).
"""

from typing import Any

import pytest

from common.config import ConfigError
from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.models.state import RunStatus
from svc_orchestrator.services.run_config import DEBATE_ROUNDS_CAP
from svc_orchestrator.testing import DiscoverHarness, FakeWorld, make_idea, make_objection

IDEATE = ("ideation", "POST /ideate")
SKEPTIC = ("ideation", "POST /skeptic")
FILTER = ("critic", "POST /objections/filter")


def endless_debate() -> FakeWorld:
    """Two fresh ideas per round, and a brand new minor objection every round, forever."""
    world = FakeWorld()
    counter = {"n": 0}

    def ideate(body: dict[str, Any]) -> Any:
        first = (body["round"] - 1) * 2 + 1
        return {
            "ideas": [make_idea(n) for n in (first, first + 1)],
            "dropped": [],
            "reason": None,
            "round": body["round"],
        }

    def skeptic(body: dict[str, Any]) -> Any:
        counter["n"] += 1
        return {
            "objections": [
                make_objection(
                    500 + counter["n"], body["ideas"][0], severity="minor", dimension="learning"
                )
            ]
        }

    world.ideate_fn, world.skeptic_fn = ideate, skeptic
    return world


async def run(h: DiscoverHarness) -> DiscoverState:
    return await h.start(count=9)  # far more than the debate can ever produce


def rounds_seen(world: FakeWorld) -> list[int]:
    return [b["round"] for b in world.bodies(*IDEATE)]


async def test_objections_that_keep_coming_stop_after_exactly_three_rounds() -> None:
    world = endless_debate()
    h = DiscoverHarness(world)
    state = await run(h)
    assert rounds_seen(world) == [1, 2, 3]  # never a 4th
    assert world.count(*SKEPTIC) == 3
    assert world.count(*FILTER) == 3
    assert state.rounds_run == 3
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None and state.card["rounds"] == 3
    assert h.event_types()[-1] == "run_completed"


@pytest.mark.parametrize("forged", [4, 10, 1000])
async def test_a_forged_config_cannot_raise_the_cap(forged: int) -> None:
    world = endless_debate()
    h = DiscoverHarness(world)
    # bypass validation the way a bug or a tampered in-memory config would
    object.__setattr__(h.ctx.config, "debate_max_rounds", forged)
    await run(h)
    assert rounds_seen(world) == [1, 2, 3]


@pytest.mark.parametrize("rounds", [4, 5, 99, 0, -1, 2.5, "3", True])
def test_a_configured_cap_outside_1_to_3_is_refused_at_startup(rounds: Any) -> None:
    with pytest.raises(ConfigError):
        DiscoverHarness(FakeWorld(), debate_max_rounds=rounds)


@pytest.mark.parametrize("cap", [1, 2, 3])
async def test_a_lower_cap_is_honoured_exactly(cap: int) -> None:
    world = endless_debate()
    state = await run(DiscoverHarness(world, debate_max_rounds=cap))
    assert rounds_seen(world) == list(range(1, cap + 1))
    assert state.rounds_run == cap


async def test_the_hard_cap_constant_is_three() -> None:
    assert DEBATE_ROUNDS_CAP == 3


async def test_round_three_is_traced_and_no_round_four_decision_exists() -> None:
    world = endless_debate()
    h = DiscoverHarness(world)
    await run(h)
    rounds = [
        r.input["round"]
        for r in h.records
        if r.kind == "decision" and r.output.get("decision") == "debate_round"
    ]
    assert rounds == [1, 2, 3]
