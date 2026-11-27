"""Internal M3 endpoints: auth, validation, 202 start, answer/approve, error mapping."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from svc_orchestrator.config import Settings
from svc_orchestrator.main import build_app
from svc_orchestrator.services.runs import RunService
from svc_orchestrator.testing import FakeWorld, Rig, fake_verdict, voi_response

TOKEN = {"X-Internal-Token": "secret"}
BODY: dict[str, Any] = {
    "kind": "validate",
    "user_id": "u1",
    "input": {"idea_text": "A CLI that lints Dockerfiles", "idea_id": "idea_1", "version": 1},
}
Make = Callable[..., tuple[httpx.AsyncClient, RunService]]


def asking_world() -> FakeWorld:
    world = FakeWorld()
    world.voi_fn = lambda body: voi_response(True)
    world.verdict_fn = lambda req: fake_verdict(
        req, "NO-GO" if req["gate_inputs"]["estimated_effort_hours"] > 150 else "GO"
    )
    return world


@pytest.fixture
async def make() -> AsyncIterator[Make]:
    services: list[RunService] = []

    def factory(world: FakeWorld | None = None) -> tuple[httpx.AsyncClient, RunService]:
        rig = Rig(world)
        service = RunService(
            context_factory=rig.context, checkpointer=MemorySaver(), config=rig.config
        )
        services.append(service)
        app = build_app(Settings(internal_token=SecretStr("secret")), service=service)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        return client, service

    yield factory
    for s in services:
        await s.aclose()


async def test_every_run_endpoint_requires_the_internal_token(make: Make) -> None:
    client, _ = make()
    for method, path in (
        ("POST", "/runs"),
        ("GET", "/runs/run_x"),
        ("POST", "/runs/run_x/answer"),
        ("POST", "/runs/run_x/approve"),
    ):
        assert (await client.request(method, path, json={})).status_code == 401
    wrong = await client.post("/runs", json=BODY, headers={"X-Internal-Token": "nope"})
    assert wrong.status_code == 401


async def test_post_runs_returns_202_and_the_run_completes(make: Make) -> None:
    client, service = make()
    response = await client.post("/runs", json=BODY, headers=TOKEN)
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "succeeded"
    assert view["result"]["verdict"] == "GO"
    assert set(view) >= {"run_id", "kind", "state", "progress", "spent", "budget", "result"}


def _kind(b: dict[str, Any]) -> None:
    b["kind"] = "discover"


def _no_user(b: dict[str, Any]) -> None:
    b.pop("user_id")


def _empty_text(b: dict[str, Any]) -> None:
    b["input"]["idea_text"] = ""


def _long_text(b: dict[str, Any]) -> None:
    b["input"]["idea_text"] = "x" * 4001


def _version0(b: dict[str, Any]) -> None:
    b["input"]["version"] = 0


def _bad_override(b: dict[str, Any]) -> None:
    b["input"]["overrides"] = {"vibes": 1}


def _extra(b: dict[str, Any]) -> None:
    b["extra"] = 1


@pytest.mark.parametrize(
    "mutate", [_kind, _no_user, _empty_text, _long_text, _version0, _bad_override, _extra]
)
async def test_invalid_start_bodies_are_422_with_the_error_envelope(
    make: Make, mutate: Callable[[dict[str, Any]], None]
) -> None:
    client, _ = make()
    body = {**BODY, "input": dict(BODY["input"])}
    mutate(body)
    response = await client.post("/runs", json=body, headers=TOKEN)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_idea_text_of_exactly_4000_chars_is_accepted(make: Make) -> None:
    client, service = make()
    body = {**BODY, "input": {**BODY["input"], "idea_text": "x" * 4000}}
    response = await client.post("/runs", json=body, headers=TOKEN)
    assert response.status_code == 202
    await service.wait(response.json()["run_id"])


async def test_unknown_run_is_404(make: Make) -> None:
    client, _ = make()
    response = await client.get("/runs/run_nope", headers=TOKEN)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_answer_flow_changes_the_verdict(make: Make) -> None:
    client, service = make(asking_world())
    run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "awaiting_input"
    qid = view["pending_question"]["question_id"]
    accepted = await client.post(
        f"/runs/{run_id}/answer", json={"question_id": qid, "answer": 300}, headers=TOKEN
    )
    assert accepted.status_code == 202
    await service.wait(run_id)
    final = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert final["result"]["verdict"] == "NO-GO"


async def test_answer_conflicts_and_validation(make: Make) -> None:
    client, service = make(asking_world())
    run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    wrong = await client.post(
        f"/runs/{run_id}/answer", json={"question_id": "q_other", "answer": 1}, headers=TOKEN
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (409, "CONFLICT")
    bad = await client.post(
        f"/runs/{run_id}/answer", json={"question_id": "q", "answer": "lots"}, headers=TOKEN
    )
    assert bad.status_code == 422
    missing = await client.post(
        "/runs/run_nope/answer", json={"question_id": "q", "answer": 1}, headers=TOKEN
    )
    assert missing.status_code == 404


async def test_approve_flow_and_decision_validation(make: Make) -> None:
    world = FakeWorld()
    world.evidence = world.evidence[:1]
    world.needs_approval = True
    client, service = make(world)
    run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "awaiting_approval"
    aid = view["pending_approval"]["approval_id"]
    bad = await client.post(
        f"/runs/{run_id}/approve", json={"approval_id": aid, "decision": "maybe"}, headers=TOKEN
    )
    assert bad.status_code == 422
    ok = await client.post(
        f"/runs/{run_id}/approve", json={"approval_id": aid, "decision": "decline"}, headers=TOKEN
    )
    assert ok.status_code == 202
    await service.wait(run_id)
    assert (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()["state"] == "succeeded"
    again = await client.post(
        f"/runs/{run_id}/approve", json={"approval_id": aid, "decision": "approve"}, headers=TOKEN
    )
    assert again.status_code == 409


async def test_probes_stay_open_without_a_token(make: Make) -> None:
    client, _ = make()
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200
