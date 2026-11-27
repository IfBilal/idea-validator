"""Behaviour: the verdict cache (CLAUDE.md rule 6, docs/harness/07).

An identical second run costs nothing but the cache read (plus the profile and the engine
config that build the key). Any version in the key changing is a miss: profile, rubric,
weights, engine and idea version, and the idea text itself.
"""

from collections.abc import Callable
from typing import Any

import pytest

from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.testing import FakeWorld, Harness, fake_verdict

CACHE_GET = ("profile", "GET /internal/cache/verdict/")
EXPENSIVE = [
    ("kb", "POST /search"),
    ("analyst", "POST /features"),
    ("scouts", "POST /scout"),
    ("validators", "POST /score-all"),
    ("verdict", "POST /verdict"),
    ("critic", "POST /check"),
    ("critic", "POST /approval/evaluate"),
]


async def run(h: Harness, **over: Any) -> ValidateState:
    await h.start(**over)
    return await h.state()


def routes(world: FakeWorld) -> list[tuple[str, str]]:
    return [(s, r) for s, r, _ in world.calls]


RUBRICS = {"current": "r1"}


def consistent_versions(world: FakeWorld) -> None:
    """M11 stamps its answer with the versions it runs under (the cache stores only those)."""
    RUBRICS["current"] = "r1"
    original = world._answer

    def answer(route: str, path: str, body: Any) -> tuple[int, Any]:
        status, payload = original(route, path, body)
        if route == "GET /config":
            payload["versions"]["rubrics"] = RUBRICS["current"]
        return status, payload

    world._answer = answer  # type: ignore[method-assign]
    world.verdict_fn = lambda req: fake_verdict(
        req,
        "GO",
        versions={
            "engine": world.engine_version,
            "rubrics": RUBRICS["current"],
            "profile_weights": world.weights_version,
        },
    )


def with_rubric_version(world: FakeWorld, version: str) -> None:
    RUBRICS["current"] = version


async def test_a_second_identical_run_only_reads_the_cache() -> None:
    first = Harness()
    first_state = await run(first)
    assert first_state.cache_hit is False
    world = first.world
    world.calls.clear()
    second = Harness(world, run_id="run_2")
    state = await run(second)
    assert state.cache_hit is True and state.status is RunStatus.SUCCEEDED
    assert not any(routes(world).count(e) for e in EXPENSIVE)
    allowed = ("GET /internal/profiles/", "GET /config", "GET /internal/cache/verdict/")
    assert all(r.startswith(allowed) for _, r in routes(world))  # key inputs and the read only
    assert sum(1 for _, r in routes(world) if r.startswith("GET /internal/cache/verdict/")) == 1
    assert not any(r.startswith("PUT") for _, r in routes(world))  # nothing re-stored


async def test_the_cached_card_is_the_same_verdict_with_the_new_run_id() -> None:
    first = Harness()
    first_state = await run(first)
    second = Harness(first.world, run_id="run_2")
    state = await run(second)
    assert first_state.card is not None and state.card is not None
    for field in ("verdict", "score", "confidence", "stability", "scores", "versions"):
        assert state.card[field] == first_state.card[field]
    assert state.card["cache_hit"] is True and state.card["run_id"] == "run_2"
    assert any(d.output.get("decision") == "cache_hit" for d in second.decisions())
    assert second.event_types()[-2:] == ["verdict", "run_completed"]


async def test_a_cache_hit_spends_almost_nothing() -> None:
    first = Harness()
    await run(first)
    second = Harness(first.world, run_id="run_2")
    await run(second)
    cold = (await first.ledger.spent()).steps
    warm = (await second.ledger.spent()).steps
    assert 0 < warm < cold


def bump_profile(world: FakeWorld) -> dict[str, Any]:
    world.profile = {**world.profile, "version": 4}
    return {}


def bump_weights(world: FakeWorld) -> dict[str, Any]:
    world.weights_version = "w2"
    return {}


def bump_engine(world: FakeWorld) -> dict[str, Any]:
    world.engine_version = "m11@v2"
    return {}


def bump_rubrics(world: FakeWorld) -> dict[str, Any]:
    with_rubric_version(world, "r2")
    return {}


MISSES: list[tuple[str, Callable[[FakeWorld], dict[str, Any]]]] = [
    ("idea_version", lambda w: {"idea_version": 2}),
    ("idea_text", lambda w: {"idea_text": "A different idea about gardening robots"}),
    ("profile_version", bump_profile),
    ("weights_version", bump_weights),
    ("engine_version", bump_engine),
    ("rubric_version", bump_rubrics),
]


@pytest.mark.parametrize(("what", "change"), MISSES, ids=[m[0] for m in MISSES])
async def test_changing_any_version_in_the_key_is_a_miss(
    what: str, change: Callable[[FakeWorld], dict[str, Any]]
) -> None:
    world = FakeWorld()
    consistent_versions(world)
    first = Harness(world)
    await run(first)
    world.calls.clear()
    over = change(world)
    second = Harness(world, run_id="run_2")
    state = await run(second, **over)
    assert state.cache_hit is False, what
    assert world.count("verdict", "POST /verdict") == 1  # recomputed by M11, not served stale
    assert len(world.cache) == 2  # the old entry stays, the new one has its own key


async def test_the_key_names_every_version() -> None:
    h = Harness()
    await run(h)
    (key,) = h.world.cache
    for part in (":idea_1:1:", ":p3:", ":rr1:", ":ww1:", ":em11@v1"):
        assert part in key


async def test_an_unchanged_world_stays_a_hit_over_many_runs() -> None:
    first = Harness()
    await run(first)
    for n in range(2, 5):
        state = await run(Harness(first.world, run_id=f"run_{n}"))
        assert state.cache_hit is True
    assert first.world.count("verdict", "POST /verdict") == 1
