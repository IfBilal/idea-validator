"""GET /runs/{id}/trace: ownership through M2 first, then a size-capped, sanitised proxy to M13."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from svc_gateway.clients.coordination import MemoryIdempotencyStore, MemoryStreamGate
from svc_gateway.config import Settings
from svc_gateway.main import build_app
from svc_gateway.ratelimit import MemoryRateLimiter
from svc_gateway.security import issue_token
from svc_gateway.testing import FakeM2, FakeM3, FakeM13, ScriptedBus, agentops_client, clients

SECRET = "s" * 40
ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())
RUN = "run_000000000001"
LEAKS = ("internal.test", "secret internal detail", "X-Internal-Token", "Traceback")


@dataclass
class Env:
    client: TestClient
    m2: FakeM2
    m3: FakeM3
    m13: FakeM13

    def headers(self, user: str = ALICE) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_token(SECRET, user, 3600)}"}


def build(with_agentops: bool = True, max_bytes: int = 1024 * 1024, **settings: Any) -> Env:
    m2, m3, m13 = FakeM2(), FakeM3(), FakeM13()
    ideas, orchestrator, profiles = clients(m2, m3)
    config = Settings(jwt_secret=SecretStr(SECRET), ui_origin="https://ui.test", **settings)
    app = build_app(
        config,
        limiter=MemoryRateLimiter(datetime.now(UTC).timestamp),
        profiles=profiles,
        ideas=ideas,
        orchestrator=orchestrator,
        events=ScriptedBus(),
        store=MemoryIdempotencyStore(),
        gate=MemoryStreamGate(),
        agentops=agentops_client(m13, max_bytes) if with_agentops else None,
    )
    return Env(TestClient(app), m2, m3, m13)


@pytest.fixture
def env() -> Iterator[Env]:
    e = build()
    with e.client:
        yield e


def own_run(env: Env, user: str = ALICE, run_id: str = RUN) -> None:
    env.m2.add_run(run_id, user)
    env.m13.add_trace(run_id)


def no_leaks(text: str) -> None:
    for leak in LEAKS:
        assert leak not in text


def test_the_owner_gets_the_trace(env: Env) -> None:
    own_run(env)
    response = env.client.get(f"/runs/{RUN}/trace", headers=env.headers())
    assert response.status_code == 200
    assert response.json()["run_id"] == RUN and response.json()["totals"]["steps"] == 2
    assert env.m13.calls == [("GET", f"/runs/{RUN}/trace", {})]


def test_flat_is_forwarded_and_only_as_a_boolean(env: Env) -> None:
    own_run(env)
    assert env.client.get(f"/runs/{RUN}/trace?flat=true", headers=env.headers()).status_code == 200
    assert env.m13.calls[-1][2] == {"flat": "true"}
    assert env.client.get(f"/runs/{RUN}/trace?flat=../x", headers=env.headers()).status_code == 422


def test_a_foreign_run_and_an_unknown_run_look_identical_and_never_reach_m13(env: Env) -> None:
    own_run(env, user=BOB)
    foreign = env.client.get(f"/runs/{RUN}/trace", headers=env.headers(ALICE))
    unknown = env.client.get("/runs/run_00000000dead/trace", headers=env.headers(ALICE))
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert env.m13.calls == []
    no_leaks(foreign.text)


def test_the_owner_sees_their_trace_and_the_other_user_still_gets_404(env: Env) -> None:
    own_run(env, user=BOB)
    assert env.client.get(f"/runs/{RUN}/trace", headers=env.headers(BOB)).status_code == 200
    assert env.client.get(f"/runs/{RUN}/trace", headers=env.headers(ALICE)).status_code == 404


@pytest.mark.parametrize("run_id", ["x", "run_1/../../runs", "run id", "a" * 80, "run%2F1"])
def test_malformed_run_ids_are_422_or_404_before_any_upstream_call(env: Env, run_id: str) -> None:
    response = env.client.get(f"/runs/{run_id}/trace", headers=env.headers())
    assert response.status_code in (404, 422)
    assert env.m13.calls == [] and env.m3.calls == []


def test_authentication_is_required(env: Env) -> None:
    own_run(env)
    assert env.client.get(f"/runs/{RUN}/trace").status_code == 401
    assert (
        env.client.get(f"/runs/{RUN}/trace", headers={"Authorization": "Bearer x"}).status_code
        == 401
    )
    assert env.m13.calls == []


def test_a_run_whose_trace_is_not_stored_is_a_plain_404(env: Env) -> None:
    env.m2.add_run(RUN, ALICE)
    response = env.client.get(f"/runs/{RUN}/trace", headers=env.headers())
    assert response.status_code == 404
    no_leaks(response.text)


@pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
def test_upstream_failures_are_generic(env: Env, status: int) -> None:
    own_run(env)
    env.m13.fail = status
    response = env.client.get(f"/runs/{RUN}/trace", headers=env.headers())
    assert response.status_code == 502
    no_leaks(response.text)


def test_an_upstream_timeout_is_a_504_without_details(env: Env) -> None:
    own_run(env)
    env.m13.timeout = True
    response = env.client.get(f"/runs/{RUN}/trace", headers=env.headers())
    assert response.status_code == 504
    no_leaks(response.text)


def test_an_answer_for_another_run_is_never_forwarded(env: Env) -> None:
    own_run(env)
    env.m13.wrong_run = True
    response = env.client.get(f"/runs/{RUN}/trace", headers=env.headers())
    assert response.status_code == 502
    assert "someoneelse" not in response.text


def test_an_oversized_answer_is_refused() -> None:
    e = build(max_bytes=2000)
    with e.client:
        own_run(e)
        e.m13.add_trace(RUN, tree=[{"record": {"pad": "x" * 5000}}])
        response = e.client.get(f"/runs/{RUN}/trace", headers=e.headers())
    assert response.status_code == 502
    assert "xxxx" not in response.text


def test_an_answer_just_under_the_cap_passes() -> None:
    e = build(max_bytes=1024 * 1024)
    with e.client:
        own_run(e)
        e.m13.add_trace(RUN, tree=[{"record": {"pad": "x" * 200_000}}])
        assert e.client.get(f"/runs/{RUN}/trace", headers=e.headers()).status_code == 200


def test_without_agentops_configured_it_is_a_503_after_the_ownership_check() -> None:
    e = build(with_agentops=False)
    with e.client:
        e.m2.add_run(RUN, BOB)
        assert e.client.get(f"/runs/{RUN}/trace", headers=e.headers(ALICE)).status_code == 404
        e.m2.add_run("run_000000000002", ALICE)
        response = e.client.get("/runs/run_000000000002/trace", headers=e.headers())
    assert response.status_code == 503


def test_traces_have_their_own_per_user_rate_limit() -> None:
    e = build(user_trace_per_min=2)
    with e.client:
        own_run(e)
        codes = [
            e.client.get(f"/runs/{RUN}/trace", headers=e.headers()).status_code for _ in range(4)
        ]
        other = e.client.get(f"/runs/{RUN}/trace", headers=e.headers(BOB)).status_code
    assert codes == [200, 200, 429, 429]
    assert other == 404  # another user is unaffected by Alice's budget


def test_readiness_does_not_depend_on_agentops(env: Env) -> None:
    env.m13.fail = 503
    response = env.client.get("/ready")
    assert response.status_code == 200
    assert "agentops" not in response.json()["checks"]


def test_the_run_listing_of_m13_is_not_exposed(env: Env) -> None:
    own_run(env)
    for path in ("/runs", "/traces", "/drift/latest", "/registry"):
        response = env.client.get(path, headers=env.headers())
        assert response.status_code in (404, 405)
    assert env.m13.calls == []
