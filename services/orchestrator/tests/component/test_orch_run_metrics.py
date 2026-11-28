"""runs_total{kind,status}: one count per run that reaches a terminal state (dashboard: budget stops)."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from svc_orchestrator.config import Settings
from svc_orchestrator.main import build_app
from svc_orchestrator.models.state import RunStatus
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
            context_factory=rig.context, checkpointer=MemorySaver(), config=rig.config
        )
        services.append(service)
        app = build_app(Settings(internal_token=SecretStr("secret")), service=service)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        return client, service

    yield factory
    for s in services:
        await s.aclose()


def count(text: str, kind: str, status: str) -> float:
    wanted = f'runs_total{{kind="{kind}",status="{status}"}}'
    for line in text.splitlines():
        if line.startswith(wanted):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def test_a_finished_run_is_counted_once(make: Make) -> None:
    client, service = make()
    run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    for _ in range(3):  # reading the run again never counts it again
        await client.get(f"/runs/{run_id}", headers=TOKEN)
    text = (await client.get("/metrics")).text
    assert count(text, "validate", "succeeded") == 1


async def test_two_runs_are_two_counts(make: Make) -> None:
    client, service = make()
    for _ in range(2):
        run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
        await service.wait(run_id)
    assert count((await client.get("/metrics")).text, "validate", "succeeded") == 2


async def test_a_run_that_has_not_finished_is_not_counted(make: Make) -> None:
    client, _ = make()
    text = (await client.get("/metrics")).text
    assert "runs_total{" not in text


async def test_every_terminal_status_is_counted_under_its_own_label(make: Make) -> None:
    client, service = make()
    seen: list[tuple[str, str]] = []
    service.on_terminal = lambda kind, status: seen.append((kind, status))
    run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    assert seen == [("validate", RunStatus.SUCCEEDED.value)]


async def test_a_failing_hook_never_breaks_the_run(make: Make) -> None:
    client, service = make()

    def boom(kind: str, status: str) -> None:
        raise RuntimeError("metrics broke")

    service.on_terminal = boom
    run_id = (await client.post("/runs", json=BODY, headers=TOKEN)).json()["run_id"]
    await service.wait(run_id)
    view = (await client.get(f"/runs/{run_id}", headers=TOKEN)).json()
    assert view["state"] == "succeeded"
