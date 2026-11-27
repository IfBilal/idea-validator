"""Review fixes: no stale verdicts, no invented effort, validated input, strict cache keys."""

from typing import Any

import pytest

from common.errors import ValidationFailed
from svc_orchestrator.models.api import StartRun
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.services.runs import RunService
from svc_orchestrator.testing import FakeWorld, Harness, fake_verdict, voi_response

VERDICT_ROUTE = ("verdict", "POST /verdict")
EFFORT = "estimated_effort_hours"


async def run(h: Harness, **over: Any) -> ValidateState:
    await h.start(**over)
    return await h.state()


def competition_unsupported(body: dict[str, Any]) -> Any:
    return {
        "results": [
            {
                "claim_id": c["claim_id"],
                "supported": c["claim_id"] != "cl_competition",
                "reason": "quote does not support",
            }
            for c in body["claims"]
        ]
    }


def spend_all_steps(h: Harness) -> None:
    h.redis.hashes.setdefault("budget:run_1", {})["steps"] = 10_000


def assert_no_final_verdict(h: Harness, state: ValidateState) -> None:
    assert state.card is not None
    assert state.card["verdict"] is None
    assert state.card["partial"] is True
    assert "verdict" not in h.event_types()
    assert h.world.cache == {}


# ---- 1. a pre-challenge verdict is never presented as final --------------------------------
async def test_recompute_failure_leaves_no_final_verdict() -> None:
    world = FakeWorld()
    world.check_fn = competition_unsupported
    world.faults[VERDICT_ROUTE] = ["pass", 503, 503, 503]
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.FAILED
    assert "RECOMPUTE" in (state.error or "")
    assert_no_final_verdict(h, state)


async def test_recompute_hitting_the_budget_leaves_no_final_verdict() -> None:
    world = FakeWorld()

    def check(body: dict[str, Any]) -> Any:
        spend_all_steps(h)  # the budget runs out right after M12 removed a claim
        return competition_unsupported(body)

    world.check_fn = check
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert world.count(*VERDICT_ROUTE) == 1
    assert_no_final_verdict(h, state)


async def test_claim_check_failure_after_a_verdict_leaves_no_final_verdict() -> None:
    world = FakeWorld()
    world.faults[("critic", "POST /check")] = [503, 503, 503]
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.FAILED
    assert state.verdict is not None  # M11 did answer, but the challenge never settled
    assert_no_final_verdict(h, state)


async def test_budget_stop_between_verdict_and_claim_check_leaves_no_final_verdict() -> None:
    world = FakeWorld()

    def engine(req: dict[str, Any]) -> Any:
        spend_all_steps(h)
        return fake_verdict(req, "GO")

    world.verdict_fn = engine
    h = Harness(world)
    state = await run(h)
    assert state.status is RunStatus.BUDGET_STOPPED
    assert world.count("critic", "POST /check") == 0
    assert_no_final_verdict(h, state)


async def test_only_a_settled_fresh_verdict_is_emitted_once() -> None:
    h = Harness()
    await run(h)
    assert h.event_types().count("verdict") == 1


async def test_after_a_successful_recompute_the_recomputed_verdict_is_final() -> None:
    world = FakeWorld()
    world.check_fn = competition_unsupported
    world.verdict_fn = lambda req: fake_verdict(
        req, "PIVOT" if any(s["score"] is None for s in req["scores"]) else "GO"
    )
    h = Harness(world)
    state = await run(h)
    assert state.card is not None
    assert state.card["verdict"] == "PIVOT"
    assert state.card["partial"] is False
    assert h.event_types().count("verdict") == 1
    event = next(e for e in h.publisher.events if e.type == "verdict")
    assert event.payload["card"]["verdict"] == "PIVOT"


# ---- 2. the effort is never invented ---------------------------------------------------------
def no_effort_world() -> FakeWorld:
    world = FakeWorld()
    world.extra = {}
    world.verdict_fn = lambda req: fake_verdict(
        req, "GO" if req["gate_inputs"][EFFORT] <= 150 else "NO-GO"
    )
    return world


def effort_values_sent(world: FakeWorld) -> list[Any]:
    return [b["gate_inputs"][EFFORT] for b in world.bodies(*VERDICT_ROUTE)]


async def test_unknown_effort_asks_a_required_question_before_calling_m11() -> None:
    world = no_effort_world()
    h = Harness(world)
    assert "__interrupt__" in await h.start()
    pending = await h.pending()
    assert pending is not None
    assert pending["question_id"] == f"q_{EFFORT}"
    assert pending["required"] is True
    assert pending["range"] == [0, 10_000]
    assert world.count(*VERDICT_ROUTE) == 0
    assert world.count("verdict", "POST /value-of-information") == 0


@pytest.mark.parametrize(("hours", "label"), [(0, "GO"), (80, "GO"), (151, "NO-GO")])
async def test_the_answer_is_sent_to_m11_exactly(hours: float, label: str) -> None:
    world = no_effort_world()
    h = Harness(world)
    await h.start()
    await h.resume(hours)
    state = await h.state()
    assert effort_values_sent(world) == [hours]
    assert state.card is not None
    assert state.card["verdict"] == label
    assert world.cache == {}  # a run that needed an answer is not cached


async def test_no_answer_means_no_verdict_and_nothing_fabricated_is_sent() -> None:
    world = no_effort_world()
    h = Harness(world)
    await h.start()
    await h.resume("timeout")
    state = await h.state()
    assert world.count(*VERDICT_ROUTE) == 0
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["verdict"] is None
    assert state.card["partial"] is True
    assert state.card["missing"] == [EFFORT]
    assert "verdict" not in h.event_types()
    assert h.event_types()[-1] == "run_completed"
    assert world.cache == {}
    for _service, _route, body in world.calls:
        assert not (isinstance(body, dict) and body.get("gate_inputs", {}).get(EFFORT) == 0)


async def test_a_user_override_means_no_effort_question() -> None:
    world = no_effort_world()
    h = Harness(world)
    await h.start(overrides={EFFORT: 60})
    assert effort_values_sent(world) == [60]


async def test_the_effort_question_counts_toward_the_cap_of_three() -> None:
    world = no_effort_world()
    world.voi_fn = lambda body: voi_response(True)
    unknowns = [
        {"scope": "profile", "field": "hours_per_week", "range": [5, 20]},
        {"scope": "profile", "field": "weeks_available", "range": [4, 20]},
        {"scope": "profile", "field": "team_size", "range": [1, 4]},
    ]
    h = Harness(world)
    result = await h.start(unknowns=unknowns)
    questions: list[str] = []
    while "__interrupt__" in result:
        pending = await h.pending()
        assert pending is not None
        questions.append(pending["field"])
        result = await h.resume(10)
    assert questions[0] == EFFORT
    assert len(questions) == 3
    assert (await h.state()).status is RunStatus.SUCCEEDED


async def test_when_the_cap_is_already_used_up_the_run_finishes_without_a_verdict() -> None:
    world = no_effort_world()
    h = Harness(world)
    state = await run(h, asked=["a", "b", "c"])  # three questions already asked
    assert world.count(*VERDICT_ROUTE) == 0
    assert state.card is not None
    assert state.card["verdict"] is None
    assert state.card["missing"] == [EFFORT]


async def test_scores_are_never_replaced_by_user_input() -> None:
    world = FakeWorld()
    await run(Harness(world), overrides={EFFORT: 60, "team_size": 3})
    sent = world.bodies(*VERDICT_ROUTE)[0]
    assert {s["score"] for s in sent["scores"]} == {3.5}
    assert sent["profile"]["team_size"] == 3
    assert sent["gate_inputs"]["n_sources"] == 4  # evidence facts come from evidence only


# ---- RunService validates answers against the question's range ----------------------------------
async def test_run_service_rejects_out_of_range_answers_and_keeps_waiting() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from svc_orchestrator.testing import Rig

    rig = Rig(no_effort_world())
    service = RunService(context_factory=rig.context, checkpointer=MemorySaver(), config=rig.config)
    body = StartRun.model_validate(
        {
            "kind": "validate",
            "user_id": "u1",
            "input": {"idea_text": "A CLI", "idea_id": "idea_1", "version": 1},
        }
    )
    run_id = await service.start(body)
    await service.wait(run_id)
    for bad in (-1, 10_001, float("nan"), float("inf")):
        with pytest.raises(ValidationFailed):
            await service.answer(run_id, f"q_{EFFORT}", bad)
    assert (await service.get(run_id)).state == "awaiting_input"
    await service.answer(run_id, f"q_{EFFORT}", 10_000)  # the boundary is accepted
    await service.wait(run_id)
    assert rig.world.bodies(*VERDICT_ROUTE)[0]["gate_inputs"][EFFORT] == 10_000
    await service.aclose()


# ---- 4. strict cache keys ----------------------------------------------------------------------
async def test_a_profile_without_a_version_fails_the_run_instead_of_defaulting() -> None:
    world = FakeWorld()
    world.profile = {k: v for k, v in world.profile.items() if k != "version"}
    state = await run(Harness(world))
    assert state.status is RunStatus.FAILED
    assert "version" in (state.error or "")
    assert world.count("kb", "POST /search") == 0
    assert world.cache == {}


@pytest.mark.parametrize(
    "tamper",
    [
        lambda card: card["versions"].update(engine="m11@v0"),
        lambda card: card["versions"].update(rubrics="r0"),
        lambda card: card["versions"].update(profile_weights="w0"),
        lambda card: card.update(idea_id="idea_other"),
        lambda card: card.update(version=7),
        lambda card: card.pop("versions"),
    ],
)
async def test_a_cached_card_that_disagrees_with_the_key_is_a_miss(tamper: Any) -> None:
    first = Harness()
    await run(first)
    key = next(iter(first.world.cache))
    tamper(first.world.cache[key])
    second = Harness(first.world, run_id="run_2")
    state = await run(second)
    assert state.cache_hit is False
    assert state.card is not None
    assert state.card["versions"] == {
        "engine": "m11@v1",
        "rubrics": "r1",
        "profile_weights": "w1",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"versions": {"engine": "m11@v9", "rubrics": "r1", "profile_weights": "w1"}},
        {"idea_id": "idea_other"},
        {"version": 9},
    ],
)
async def test_a_verdict_that_disagrees_with_the_key_is_not_stored(
    override: dict[str, Any],
) -> None:
    world = FakeWorld()
    world.verdict_fn = lambda req: fake_verdict(req, "GO", **override)
    state = await run(Harness(world))
    assert state.status is RunStatus.SUCCEEDED
    assert state.card is not None
    assert state.card["verdict"] == "GO"  # still shown, just not cached
    assert world.cache == {}
    assert any("not cached" in n for n in state.notes)


# ---- 5. no request body carries a verdict (recursive scan) -------------------------------------
def contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(contains_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(contains_key(v, key) for v in value)
    return False


def is_storage(service: str, route: str) -> bool:
    return service == "profile" and route.startswith(
        ("PUT /internal/cache/verdict/", "PUT /internal/runs/")
    )


async def test_no_request_body_carries_a_verdict_even_nested() -> None:
    world = FakeWorld()
    h = Harness(world)
    await run(h)
    assert contains_key({"a": [{"b": {"verdict": 1}}]}, "verdict")  # the scanner itself works
    scanned = 0
    for service, route, body in world.calls:
        if is_storage(service, route) or body is None:
            continue
        scanned += 1
        assert not contains_key(body, "verdict"), (service, route)
    assert scanned >= 9  # every module call was inspected, storage excluded


async def test_storage_bodies_carry_the_m11_card_unchanged() -> None:
    world = FakeWorld()
    responses: list[dict[str, Any]] = []

    def engine(req: dict[str, Any]) -> Any:
        verdict = fake_verdict(req, "PIVOT", score=61.25, confidence=0.66, stability="BORDERLINE")
        responses.append(verdict)
        return verdict

    world.verdict_fn = engine
    h = Harness(world)
    await run(h)
    sent = responses[-1]
    keys = ("verdict", "stability", "score", "confidence", "gates", "thresholds", "versions")
    (cached,) = world.cache.values()
    assert {k: cached[k] for k in keys} == {k: sent[k] for k in keys}
    assert cached["idea_id"] == "idea_1"
    assert cached["version"] == 1
