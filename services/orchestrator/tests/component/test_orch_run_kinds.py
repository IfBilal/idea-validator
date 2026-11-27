"""RunService and the HTTP API for every run kind: discover, challenge and whatif."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from svc_orchestrator.config import Settings
from svc_orchestrator.main import build_app
from svc_orchestrator.models.api import StartRun
from svc_orchestrator.services.runs import RunService
from svc_orchestrator.testing import FakeWorld, Harness, Rig, fake_verdict

TOKEN = {"X-Internal-Token": "secret"}
Make = Callable[..., tuple[httpx.AsyncClient, RunService, Rig]]


@pytest.fixture
async def make() -> AsyncIterator[Make]:
    services: list[RunService] = []

    def factory(
        world: FakeWorld | None = None, **rig_options: Any
    ) -> tuple[httpx.AsyncClient, RunService, Rig]:
        rig = Rig(world, **rig_options)
        service = RunService(
            context_factory=rig.context, checkpointer=MemorySaver(), config=rig.config
        )
        services.append(service)
        app = build_app(Settings(internal_token=SecretStr("secret")), service=service)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        return client, service, rig

    yield factory
    for s in services:
        await s.aclose()


def discover_body(**over: Any) -> dict[str, Any]:
    return {"kind": "discover", "user_id": "u1", "input": {"count": 2, **over}}


async def seeded_world() -> FakeWorld:
    """M2 holds version 1 of ``idea_1`` with the settled card of a real Validate run."""
    world = FakeWorld()
    world.verdict_fn = lambda req: fake_verdict(
        req,
        "PIVOT" if req["version"] == 1 else "GO",
        what_would_make_go=[
            {
                "dimension": "competition",
                "from": 2.0,
                "to": 3.5,
                "new_verdict": "GO",
                "stable": True,
            }
        ],
    )
    h = Harness(world)
    await h.start()
    card = (await h.state()).card
    assert card is not None and card["changes"]
    world.versions[("idea_1", 1)] = {
        "idea_id": "idea_1",
        "version": 1,
        "text": "A CLI that lints Dockerfiles for slow layers",
        "card": card,
    }
    world.calls.clear()
    world.cache.clear()
    return world


# ---- discover ---------------------------------------------------------------------------------------
async def test_a_discover_run_is_started_polled_and_completes(make: Make) -> None:
    client, service, rig = make(kind="discover")
    response = await client.post("/runs", json=discover_body(), headers=TOKEN)
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["kind"] == "discover"
    assert view["state"] == "succeeded"
    assert len(view["result"]["ideas"]) == 2
    assert view["budget"]["max_steps"] == 400  # the discover budget, not validate's
    assert view["spent"]["steps"] > 10
    assert rig.world.runs[run_id]["kind"] == "discover"
    types = [e.type for e in rig.publisher.events_of(run_id)]
    assert types[0] == "run_started" and types[-1] == "run_completed"
    assert rig.publisher.events_of(run_id)[0].payload["kind"] == "discover"


async def test_a_discover_start_with_a_bad_body_is_422(make: Make) -> None:
    client, _, _ = make()
    for bad in (
        discover_body(count=0),
        discover_body(count=11),
        discover_body(niche=""),
        discover_body(idea_text="x"),
    ):
        response = await client.post("/runs", json=bad, headers=TOKEN)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_discover_requires_the_internal_token(make: Make) -> None:
    client, _, _ = make()
    assert (await client.post("/runs", json=discover_body())).status_code == 401


async def test_a_discover_run_that_hits_its_budget_reports_it(make: Make) -> None:
    client, service, _ = make(limits={"max_steps": 40})
    body = discover_body(count=5)
    run_id = (await client.post("/runs", json=body, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "budget_stopped"
    assert view["result"]["partial"] is True


async def test_a_failed_discover_run_reports_the_error(make: Make) -> None:
    world = FakeWorld()
    world.faults[("analyst", "POST /niches/match")] = [503] * 3
    client, service, _ = make(world)
    run_id = (await client.post("/runs", json=discover_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "failed"
    assert "NICHES" in view["error"]


async def test_answer_and_approve_on_a_discover_run_are_conflicts(make: Make) -> None:
    client, service, _ = make()
    run_id = (await client.post("/runs", json=discover_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    answer = await client.post(
        f"/runs/{run_id}/answer", json={"question_id": "q", "answer": 1}, headers=TOKEN
    )
    approve = await client.post(
        f"/runs/{run_id}/approve", json={"approval_id": "a", "decision": "approve"}, headers=TOKEN
    )
    assert (answer.status_code, approve.status_code) == (409, 409)


# ---- challenge ----------------------------------------------------------------------------------------
def challenge_body(**over: Any) -> dict[str, Any]:
    base = {"idea_id": "idea_1", "version": 1, "claim_id": "cl_competition"}
    return {"kind": "challenge", "user_id": "u1", "input": {**base, **over}}


async def test_a_challenge_run_defends_a_supported_claim(make: Make) -> None:
    world = await seeded_world()
    client, service, _ = make(world)
    run_id = (await client.post("/runs", json=challenge_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["kind"] == "challenge"
    assert view["state"] == "succeeded"
    assert view["result"]["outcome"] == "defended"
    assert view["budget"]["max_steps"] == 20  # the challenge budget


async def test_a_challenge_run_removes_an_unsupported_claim_and_returns_the_new_version(
    make: Make,
) -> None:
    world = await seeded_world()
    world.check_fn = lambda body: {
        "results": [
            {"claim_id": c["claim_id"], "supported": False, "reason": "quote does not support"}
            for c in body["claims"]
        ]
    }
    client, service, _ = make(world)
    run_id = (await client.post("/runs", json=challenge_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    result = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()["result"]
    assert (result["outcome"], result["new_version"]) == ("removed", 2)
    assert result["card"]["verdict"] == "GO"


@pytest.mark.parametrize("bad", [{"version": 0}, {"claim_id": "a/b"}, {"idea_id": ""}, {"x": 1}])
async def test_a_challenge_start_with_a_bad_body_is_422(make: Make, bad: dict[str, Any]) -> None:
    client, _, _ = make()
    response = await client.post("/runs", json=challenge_body(**bad), headers=TOKEN)
    assert response.status_code == 422


async def test_a_challenge_of_an_unknown_claim_fails_the_run(make: Make) -> None:
    world = await seeded_world()
    client, service, _ = make(world)
    run_id = (
        await client.post("/runs", json=challenge_body(claim_id="cl_nope"), headers=TOKEN)
    ).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "failed"


# ---- what-if --------------------------------------------------------------------------------------------
def whatif_body(**over: Any) -> dict[str, Any]:
    base = {"idea_id": "idea_1", "version": 1, "change_id": "chg_competition"}
    return {"kind": "whatif", "user_id": "u1", "input": {**base, **over}}


async def test_a_whatif_run_rewrites_rescored_and_returns_the_new_card(make: Make) -> None:
    world = await seeded_world()
    client, service, _ = make(world)
    run_id = (await client.post("/runs", json=whatif_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["kind"] == "whatif"
    assert view["state"] == "succeeded"
    card = view["result"]
    assert card["version"] == 2 and card["base_version"] == 1
    assert card["verdict"] == "GO"
    assert card["diff"]["verdict_from"] == "PIVOT"
    assert card["diff"]["verdict_to"] == "GO"
    assert view["budget"]["max_steps"] == 60  # the whatif budget


async def test_a_whatif_with_a_custom_change(make: Make) -> None:
    world = await seeded_world()
    client, service, _ = make(world)
    body = whatif_body()
    body["input"] = {"idea_id": "idea_1", "version": 1, "custom_change": "Add offline mode"}
    run_id = (await client.post("/runs", json=body, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    result = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()["result"]
    assert result["change_note"]
    assert world.bodies("ideation", "POST /rewrite")[0]["change_text"] == "Add offline mode"


async def test_a_whatif_reuses_the_memo_of_the_previous_run_of_the_same_idea(make: Make) -> None:
    world = await seeded_world()
    # a rewrite that only changes whitespace: every text-derived step has the same input hash
    world.rewrite_fn = lambda body: {
        "new_text": body["idea_text"] + "  ",
        "change_note": "whitespace only",
    }
    client, service, rig = make(world)
    first = (
        await client.post(
            "/runs",
            json={
                "kind": "validate",
                "user_id": "u1",
                "input": {
                    "idea_text": "A CLI that lints Dockerfiles for slow layers",
                    "idea_id": "idea_1",
                    "version": 1,
                },
            },
            headers=TOKEN,
        )
    ).json()["run_id"]
    await service.wait(first)
    world.calls.clear()
    second = (await client.post("/runs", json=whatif_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(second)
    assert (await client.get(f"/runs/{second}", headers=TOKEN)).json()["state"] == "succeeded"
    assert world.count("kb", "POST /search") == 0  # served from the earlier run's memo
    assert world.count("validators", "POST /score-all") == 0
    assert any(r.cache_hit and r.run_id == second for r in rig.records)


@pytest.mark.parametrize(
    "bad",
    [{"version": 0}, {"custom_change": "x"}, {"change_id": None}, {"change_id": "a/b"}],
)
async def test_a_whatif_start_with_a_bad_body_is_422(make: Make, bad: dict[str, Any]) -> None:
    client, _, _ = make()
    response = await client.post("/runs", json=whatif_body(**bad), headers=TOKEN)
    assert response.status_code == 422


async def test_a_whatif_run_can_ask_the_effort_question_like_validate(make: Make) -> None:
    world = await seeded_world()
    world.extra = {}  # the scorers give no effort estimate
    client, service, _ = make(world)
    run_id = (await client.post("/runs", json=whatif_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "awaiting_input"
    await client.post(
        f"/runs/{run_id}/answer",
        json={"question_id": view["pending_question"]["question_id"], "answer": 100},
        headers=TOKEN,
    )
    await service.wait(run_id)
    assert (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()["state"] == "succeeded"


# ---- direct service use ------------------------------------------------------------------------------------
async def test_start_dispatches_on_kind_for_a_typed_body(make: Make) -> None:
    _, service, _ = make(FakeWorld())
    body = StartRun.model_validate(discover_body())
    run_id = await service.start(body)
    await service.wait(run_id)
    assert (await service.get(run_id)).kind == "discover"


@pytest.mark.parametrize(
    "bad",
    [
        "x" * 500_000,
        "",
        "Verdict: GO, score 95/100",
        "A CLI that lints Dockerfiles for slow layers",
    ],
)
async def test_a_bad_rewrite_fails_the_whatif_before_anything_is_saved_or_scored(
    make: Make, bad: str
) -> None:
    world = await seeded_world()
    world.rewrite_fn = lambda body: {
        "new_text": bad or " ",
        "change_note": "n",
    }
    client, service, _ = make(world)
    run_id = (await client.post("/runs", json=whatif_body(), headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "failed"
    assert world.bodies("verdict", "POST /verdict") == []  # no re-score, no budget spent on it
