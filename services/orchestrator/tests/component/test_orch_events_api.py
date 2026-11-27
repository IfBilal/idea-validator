"""GET /runs/{id}/events: stored events replayed after a restart (the gateway's SSE reads this)."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from common.schemas import RunEvent
from svc_orchestrator.config import Settings
from svc_orchestrator.main import build_app
from svc_orchestrator.services.runs import RunService
from svc_orchestrator.testing import FakeWorld, Rig

TOKEN = {"X-Internal-Token": "secret"}
BODY: dict[str, Any] = {
    "kind": "validate",
    "user_id": "u1",
    "input": {"idea_text": "A CLI that lints Dockerfiles", "idea_id": "idea_1", "version": 1},
}
Make = Callable[[], tuple[httpx.AsyncClient, RunService]]


@pytest.fixture
async def make() -> AsyncIterator[Make]:
    services: list[RunService] = []

    def factory() -> tuple[httpx.AsyncClient, RunService]:
        rig = Rig(FakeWorld())
        service = RunService(
            context_factory=rig.context,
            checkpointer=MemorySaver(),
            config=rig.config,
            event_log=rig.publisher,
        )
        services.append(service)
        app = build_app(Settings(internal_token=SecretStr("secret")), service=service)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ), service

    yield factory
    for s in services:
        await s.aclose()


async def started(client: httpx.AsyncClient, service: RunService) -> str:
    run_id: str = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    return run_id


async def test_the_stored_events_of_a_run_are_returned_in_order(make: Make) -> None:
    client, service = make()
    run_id = await started(client, service)
    response = await client.get(f"/runs/{run_id}/events", headers=TOKEN)
    assert response.status_code == 200
    events = [RunEvent.model_validate(e) for e in response.json()["events"]]
    assert events[0].type == "run_started" and events[-1].type == "run_completed"
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert {e.run_id for e in events} == {run_id}


async def test_after_returns_only_later_events(make: Make) -> None:
    client, service = make()
    run_id = await started(client, service)
    everything = (await client.get(f"/runs/{run_id}/events", headers=TOKEN)).json()["events"]
    later = (await client.get(f"/runs/{run_id}/events?after=3", headers=TOKEN)).json()["events"]
    assert later == everything[3:]
    last = everything[-1]["seq"]
    assert (await client.get(f"/runs/{run_id}/events?after={last}", headers=TOKEN)).json() == {
        "events": []
    }


async def test_an_unknown_run_has_no_events_rather_than_an_error(make: Make) -> None:
    client, _ = make()
    response = await client.get("/runs/run_nope/events", headers=TOKEN)
    assert (response.status_code, response.json()) == (200, {"events": []})


@pytest.mark.parametrize("after", ["-1", "abc", "1.5", "99999999999999999999"])
async def test_a_bad_after_is_a_validation_error(make: Make, after: str) -> None:
    client, _ = make()
    response = await client.get(f"/runs/run_1/events?after={after}", headers=TOKEN)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_the_events_endpoint_needs_the_internal_token(make: Make) -> None:
    client, _ = make()
    assert (await client.get("/runs/run_1/events")).status_code == 401
    assert (
        await client.get("/runs/run_1/events", headers={"X-Internal-Token": "x"})
    ).status_code == 401


async def test_a_run_id_with_odd_characters_is_refused(make: Make) -> None:
    client, _ = make()
    response = await client.get("/runs/run%20x;drop/events", headers=TOKEN)
    assert response.status_code == 422


async def test_events_are_plain_run_event_json(make: Make) -> None:
    client, service = make()
    run_id = await started(client, service)
    raw = (await client.get(f"/runs/{run_id}/events", headers=TOKEN)).text
    first = json.loads(raw)["events"][0]
    assert set(first) == {"run_id", "seq", "ts", "type", "payload"}
