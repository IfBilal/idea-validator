"""Run, idea, compare, challenge, what-if and SSE endpoints with M2/M3/Redis faked underneath."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from svc_gateway.clients.coordination import MemoryIdempotencyStore, MemoryStreamGate
from svc_gateway.config import Settings
from svc_gateway.main import build_app
from svc_gateway.ratelimit import MemoryRateLimiter
from svc_gateway.security import AUDIENCE, ISSUER, issue_token
from svc_gateway.testing import FakeM2, FakeM3, ScriptedBus, clients, make_event

SECRET = "s" * 40
ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())
LEAKS = ("internal.test", "secret internal detail", "secret-host", "X-Internal-Token", "Traceback")
CARD = {
    "verdict": "GO",
    "claims": [{"claim_id": "c1", "text": "Students complain", "evidence_ids": ["card_1#c1"]}],
}


@dataclass
class Env:
    client: TestClient
    m2: FakeM2
    m3: FakeM3
    bus: ScriptedBus
    store: MemoryIdempotencyStore
    gate: MemoryStreamGate

    def headers(self, user: str = ALICE) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_token(SECRET, user, 3600)}"}


def build(bus: ScriptedBus | None = None, **settings: Any) -> Env:
    m2, m3, bus = FakeM2(), FakeM3(), bus or ScriptedBus()
    store, gate = MemoryIdempotencyStore(), MemoryStreamGate()
    ideas, orchestrator, profiles = clients(m2, m3)
    config = Settings(jwt_secret=SecretStr(SECRET), ui_origin="https://ui.test", **settings)
    app = build_app(
        config,
        limiter=MemoryRateLimiter(datetime.now(UTC).timestamp),
        profiles=profiles,
        ideas=ideas,
        orchestrator=orchestrator,
        events=bus,
        store=store,
        gate=gate,
    )
    return Env(TestClient(app), m2, m3, bus, store, gate)


@pytest.fixture
def env() -> Iterator[Env]:
    e = build()
    with e.client:
        yield e


def no_leaks(response: Any) -> None:
    for leak in LEAKS:
        assert leak not in response.text, leak
        assert leak not in str(dict(response.headers)), leak


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def seed_run(env: Env, user: str = ALICE, run_id: str = "run_000000000001", **fields: Any) -> str:
    env.m2.add_run(run_id, user)
    env.m3.runs[run_id] = {"kind": "validate", "state": "running", "input": {}, **fields}
    return run_id


# =========================================================================================
# authentication: every endpoint, every kind of bad credential, nothing reaches M2 or M3
# =========================================================================================
IDEA = str(uuid.uuid4())
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/validate", {"idea_text": "x", "title": "t"}),
    ("POST", "/discover", {"count": 3}),
    ("GET", "/runs/run_1", None),
    ("POST", "/runs/run_1/answer", {"question_id": "q", "answer": 1}),
    ("POST", "/runs/run_1/approve", {"approval_id": "a", "decision": "approve"}),
    ("GET", "/runs/run_1/events", None),
    ("GET", "/runs/run_1/trace", None),
    ("GET", "/ideas", None),
    ("GET", f"/ideas/{IDEA}/versions", None),
    ("GET", f"/ideas/{IDEA}/versions/1", None),
    ("GET", f"/compare?ids={IDEA},{uuid.uuid4()}", None),
    ("POST", f"/ideas/{IDEA}/challenge", {"version": 1, "claim_id": "c1"}),
    ("POST", f"/ideas/{IDEA}/whatif", {"version": 1, "custom_change": "x"}),
]


def call(env: Env, method: str, path: str, body: Any, headers: dict[str, str]) -> Any:
    return env.client.request(method, path, json=body, headers=headers)


def forged(**changes: Any) -> dict[str, str]:
    claims: dict[str, Any] = {
        "sub": ALICE,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    claims.update(changes)
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


BAD_AUTH: list[tuple[str, dict[str, str]]] = [
    ("missing", {}),
    ("wrong scheme", {"Authorization": "Basic abc"}),
    ("empty bearer", {"Authorization": "Bearer "}),
    ("garbage", {"Authorization": "Bearer not.a.jwt"}),
    ("expired", forged(exp=datetime.now(UTC) - timedelta(seconds=5))),
    ("wrong audience", forged(aud="other")),
    ("subject not a uuid", forged(sub="../admin")),
    (
        "signed with another key",
        {"Authorization": "Bearer " + jwt.encode({"sub": ALICE}, "k" * 40)},
    ),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize(("case", "headers"), BAD_AUTH, ids=[c for c, _ in BAD_AUTH])
def test_every_endpoint_rejects_bad_credentials_before_touching_upstreams(
    env: Env, method: str, path: str, body: Any, case: str, headers: dict[str, str]
) -> None:
    response = call(env, method, path, body, headers)
    assert response.status_code == 401, case
    assert error_code(response) == "UNAUTHORIZED"
    assert env.m2.calls == [] and env.m3.calls == []
    no_leaks(response)


@pytest.mark.parametrize(
    ("method", "path", "body"), ENDPOINTS[:2] + ENDPOINTS[3:5] + ENDPOINTS[-2:]
)
def test_authentication_is_checked_before_the_body_is_validated(
    env: Env, method: str, path: str, body: Any
) -> None:
    assert call(env, method, path, {"nonsense": True}, {}).status_code == 401


# =========================================================================================
# POST /validate
# =========================================================================================
def validate(env: Env, user: str = ALICE, **body: Any) -> Any:
    payload = {"idea_text": "An app that plans study time", "title": "Study planner", **body}
    return env.client.post("/validate", json=payload, headers=env.headers(user))


def test_validate_a_new_idea_creates_it_starts_the_run_and_records_ownership(env: Env) -> None:
    response = validate(env)
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"run_id", "idea_id", "version"}
    assert body["version"] == 1
    assert env.m2.ideas[body["idea_id"]]["user_id"] == ALICE
    start = env.m3.posts("/runs")[0][2]
    assert start == {
        "kind": "validate",
        "user_id": ALICE,
        "input": {
            "idea_text": "An app that plans study time",
            "idea_id": body["idea_id"],
            "version": 1,
            "title": "Study planner",
        },
    }
    assert env.m2.runs[body["run_id"]]["user_id"] == ALICE
    assert env.m2.runs[body["run_id"]]["kind"] == "validate"


def test_the_user_is_always_the_token_never_the_body(env: Env) -> None:
    response = validate(env, user_id=BOB)
    assert response.status_code == 422
    assert env.m3.calls == []


def test_validating_an_existing_idea_with_new_text_adds_a_version(env: Env) -> None:
    idea_id = env.m2.add_idea(ALICE, text="old text")
    response = validate(env, idea_id=idea_id, idea_text="new text")
    assert response.status_code == 202
    assert response.json()["version"] == 2
    assert [v["text"] for v in env.m2.versions[idea_id]] == ["old text", "new text"]
    assert env.m3.posts("/runs")[0][2]["input"]["version"] == 2


def test_validating_unchanged_text_reuses_the_latest_version(env: Env) -> None:
    idea_id = env.m2.add_idea(ALICE, text="same text")
    response = validate(env, idea_id=idea_id, idea_text="same text", title=None)
    assert response.status_code == 202
    assert response.json()["version"] == 1
    assert len(env.m2.versions[idea_id]) == 1


def test_validating_someone_elses_idea_is_a_404_and_starts_nothing(env: Env) -> None:
    idea_id = env.m2.add_idea(BOB, text="bob's idea")
    response = validate(env, idea_id=idea_id, idea_text="mine now")
    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"
    assert env.m3.calls == []
    assert len(env.m2.versions[idea_id]) == 1


def test_validating_an_unknown_idea_looks_exactly_like_someone_elses(env: Env) -> None:
    foreign = env.m2.add_idea(BOB)
    a = validate(env, idea_id=foreign)
    b = validate(env, idea_id=str(uuid.uuid4()))
    assert (a.status_code, a.json()["error"]["message"]) == (
        b.status_code,
        b.json()["error"]["message"],
    )


@pytest.mark.parametrize(
    "body",
    [
        {"idea_text": "", "title": "t"},
        {"idea_text": "x" * 4001, "title": "t"},
        {"idea_text": "x", "title": "t" * 201},
        {"idea_text": "x"},
        {"title": "t"},
        {"idea_text": "x", "title": "t", "idea_id": "nope"},
        {"idea_text": "x", "title": "t", "extra": 1},
        {"idea_text": 5, "title": "t"},
        {"idea_text": "x", "title": ["t"]},
        {},
    ],
)
def test_invalid_validate_bodies_are_422_and_touch_nothing(env: Env, body: dict[str, Any]) -> None:
    response = env.client.post("/validate", json=body, headers=env.headers())
    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"
    assert env.m2.calls == [] and env.m3.calls == []


@pytest.mark.parametrize("length", [1, 4000])
def test_validate_idea_text_boundaries_are_accepted(env: Env, length: int) -> None:
    assert validate(env, idea_text="x" * length).status_code == 202


@pytest.mark.parametrize("raw", ["{not json", "[]", "null", '"text"', "123", ""])
def test_malformed_json_bodies_are_422(env: Env, raw: str) -> None:
    headers = {**env.headers(), "Content-Type": "application/json"}
    assert env.client.post("/validate", content=raw, headers=headers).status_code == 422


def test_non_json_content_type_is_rejected(env: Env) -> None:
    headers = {**env.headers(), "Content-Type": "text/plain"}
    response = env.client.post(
        "/validate", content='{"idea_text":"x","title":"t"}', headers=headers
    )
    assert response.status_code == 422
    assert env.m3.calls == []


def test_bodies_over_the_limit_are_413(env: Env) -> None:
    response = env.client.post("/validate", content=b"x" * 40_000, headers=env.headers())
    assert response.status_code == 413
    assert env.m2.calls == [] and env.m3.calls == []


def test_an_orchestrator_outage_is_a_public_502_without_internals(env: Env) -> None:
    env.m3.fail = 500
    response = validate(env)
    assert response.status_code == 502
    assert error_code(response) == "UPSTREAM_ERROR"
    no_leaks(response)


def test_an_orchestrator_timeout_is_a_504(env: Env) -> None:
    env.m3.timeout = True
    response = validate(env)
    assert response.status_code == 504
    assert error_code(response) == "UPSTREAM_TIMEOUT"
    no_leaks(response)


def test_a_profile_service_outage_is_a_public_502_and_no_run_starts(env: Env) -> None:
    env.m2.fail = 503
    response = validate(env)
    assert response.status_code == 502
    assert env.m3.calls == []
    no_leaks(response)


def test_an_orchestrator_that_refuses_the_run_kind_is_not_a_500(env: Env) -> None:
    env.m3.fail = 422
    response = validate(env)
    assert response.status_code == 422
    no_leaks(response)


def test_run_start_is_rate_limited_per_user_with_retry_after() -> None:
    e = build(user_runs_per_min=2)
    with e.client:
        assert [validate(e, idea_text=f"idea {i}").status_code for i in range(2)] == [202, 202]
        blocked = validate(e, idea_text="idea 3")
        assert blocked.status_code == 429
        assert error_code(blocked) == "RATE_LIMITED"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert validate(e, user=BOB).status_code == 202  # another user has their own budget
        assert len(e.m3.posts("/runs")) == 3  # the blocked request never reached M3


def test_the_run_budget_is_shared_by_validate_discover_challenge_and_whatif() -> None:
    e = build(user_runs_per_min=2)
    with e.client:
        idea = e.m2.add_idea(ALICE, card=CARD, verdict="GO", score=80)
        assert validate(e).status_code == 202
        assert e.client.post("/discover", json={}, headers=e.headers()).status_code == 202
        r = e.client.post(
            f"/ideas/{idea}/challenge", json={"version": 1, "claim_id": "c1"}, headers=e.headers()
        )
        assert r.status_code == 429


def test_the_per_ip_limit_still_applies_to_the_new_routes() -> None:
    e = build(ip_per_min=3)
    with e.client:
        codes = [e.client.get("/ideas", headers=e.headers()).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]


# =========================================================================================
# POST /discover
# =========================================================================================
def discover(env: Env, user: str = ALICE, **body: Any) -> Any:
    return env.client.post("/discover", json=body, headers=env.headers(user))


def test_discover_starts_a_run_with_defaults(env: Env) -> None:
    response = discover(env)
    assert response.status_code == 202
    assert set(response.json()) == {"run_id"}
    start = env.m3.posts("/runs")[0][2]
    assert start == {
        "kind": "discover",
        "user_id": ALICE,
        "input": {"niche": None, "count": 5, "deep_search": False},
    }
    assert env.m2.runs[response.json()["run_id"]] == {
        "user_id": ALICE,
        "kind": "discover",
        "state": "running",
        "input": {"niche": None, "count": 5, "deep_search": False},
    }


def test_discover_passes_the_options_through(env: Env) -> None:
    assert discover(env, niche="devtools", count=10, deep_search=True).status_code == 202
    assert env.m3.posts("/runs")[0][2]["input"] == {
        "niche": "devtools",
        "count": 10,
        "deep_search": True,
    }


@pytest.mark.parametrize("count", [0, 11, -1, "3", 2.5, True, None])
def test_discover_count_outside_1_to_10_is_422(env: Env, count: Any) -> None:
    assert discover(env, count=count).status_code == 422
    assert env.m3.calls == []


@pytest.mark.parametrize("count", [1, 10])
def test_discover_count_boundaries_are_accepted(env: Env, count: int) -> None:
    assert discover(env, count=count).status_code == 202


@pytest.mark.parametrize(
    "body", [{"niche": ""}, {"niche": "n" * 81}, {"deep_search": "yes"}, {"x": 1}]
)
def test_discover_rejects_bad_options(env: Env, body: dict[str, Any]) -> None:
    assert discover(env, **body).status_code == 422


# =========================================================================================
# GET /runs/{id}, answer, approve: ownership
# =========================================================================================
def test_a_user_can_read_their_own_run(env: Env) -> None:
    run_id = seed_run(env, result={"verdict": "GO"})
    response = env.client.get(f"/runs/{run_id}", headers=env.headers())
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["state"] == "running"
    assert body["result"] == {"verdict": "GO"}
    assert set(body) == {
        "run_id", "kind", "state", "progress", "spent", "budget",
        "pending_question", "pending_approval", "result", "error",
    }  # fmt: skip
    no_leaks(response)
    assert "internal_only" not in response.text


def test_someone_elses_run_is_a_404_identical_to_a_missing_one(env: Env) -> None:
    run_id = seed_run(env, user=BOB)
    other = env.client.get(f"/runs/{run_id}", headers=env.headers())
    missing = env.client.get("/runs/run_doesnotexist", headers=env.headers())
    assert other.status_code == missing.status_code == 404
    assert other.json()["error"]["message"] == missing.json()["error"]["message"]
    assert all(c[1] != f"/runs/{run_id}" for c in env.m3.calls)  # M3 was never asked


def test_a_run_m3_has_forgotten_falls_back_to_the_stored_record(env: Env) -> None:
    env.m2.add_run("run_old", ALICE, state="succeeded")
    response = env.client.get("/runs/run_old", headers=env.headers())
    assert response.status_code == 200
    assert (response.json()["state"], response.json()["kind"]) == ("succeeded", "validate")


@pytest.mark.parametrize("run_id", ["a" * 65, "run id", "run$1", "run.1", "..", "%2e%2e", "run%00"])
def test_malformed_run_ids_are_rejected_without_calling_upstreams(env: Env, run_id: str) -> None:
    response = env.client.get(f"/runs/{run_id}", headers=env.headers())
    assert response.status_code in (404, 422)
    assert env.m2.calls == [] and env.m3.calls == []


def test_run_id_length_boundary(env: Env) -> None:
    env.m2.add_run("r" * 64, ALICE)
    assert env.client.get("/runs/" + "r" * 64, headers=env.headers()).status_code == 200


def test_m3_failure_while_reading_a_run_is_public_safe(env: Env) -> None:
    seed_run(env)
    env.m3.fail = 500
    response = env.client.get("/runs/run_000000000001", headers=env.headers())
    assert response.status_code == 502
    no_leaks(response)


def test_answer_forwards_to_m3_for_the_owner(env: Env) -> None:
    run_id = seed_run(env)
    response = env.client.post(
        f"/runs/{run_id}/answer",
        json={"question_id": "q_effort", "answer": "120"},
        headers=env.headers(),
    )
    assert response.status_code == 202
    assert response.json() == {"run_id": run_id, "state": "running"}
    assert env.m3.calls[-1] == (
        "POST",
        f"/runs/{run_id}/answer",
        {"question_id": "q_effort", "answer": 120.0},
    )


def test_approve_forwards_to_m3_for_the_owner(env: Env) -> None:
    run_id = seed_run(env)
    response = env.client.post(
        f"/runs/{run_id}/approve",
        json={"approval_id": f"ap_{run_id}_deep_search", "decision": "decline"},
        headers=env.headers(),
    )
    assert response.status_code == 202
    assert env.m3.calls[-1][2] == {
        "approval_id": f"ap_{run_id}_deep_search",
        "decision": "decline",
    }


@pytest.mark.parametrize("action", ["answer", "approve"])
def test_answering_or_approving_someone_elses_run_is_404_and_never_reaches_m3(
    env: Env, action: str
) -> None:
    run_id = seed_run(env, user=BOB)
    body = (
        {"question_id": "q", "answer": 1}
        if action == "answer"
        else {"approval_id": "a", "decision": "approve"}
    )
    response = env.client.post(f"/runs/{run_id}/{action}", json=body, headers=env.headers())
    assert response.status_code == 404
    assert env.m3.posts(f"/{action}") == []


def test_a_non_owner_approve_never_reaches_m3_or_an_m2_approval_decision(env: Env) -> None:
    run_id = seed_run(env, user=BOB)
    r = env.client.post(
        f"/runs/{run_id}/approve",
        json={"approval_id": f"ap_{run_id}_deep_search", "decision": "approve"},
        headers=env.headers(),
    )
    assert r.status_code == 404
    assert env.m3.calls == []
    assert [c for c in env.m2.calls if "/approvals" in c[1]] == []


@pytest.mark.parametrize("action", ["answer", "approve"])
def test_a_conflict_from_m3_is_a_generic_409(env: Env, action: str) -> None:
    run_id = seed_run(env)
    env.m3.conflict = True
    body = (
        {"question_id": "q_effort", "answer": 1}
        if action == "answer"
        else {"approval_id": f"ap_{run_id}_deep_search", "decision": "approve"}
    )
    response = env.client.post(f"/runs/{run_id}/{action}", json=body, headers=env.headers())
    assert response.status_code == 409
    assert error_code(response) == "CONFLICT"
    no_leaks(response)


@pytest.mark.parametrize(
    "body",
    [
        {"question_id": "q"},
        {"answer": 1},
        {"question_id": "q", "answer": "abc"},
        {"question_id": "q", "answer": "1" * 1001},
        {"question_id": "", "answer": 1},
        {"question_id": "q", "answer": 1, "x": 1},
        {"question_id": "q", "answer": None},
        {"question_id": "q", "answer": [1]},
    ],
)
def test_bad_answers_are_422_and_never_reach_m3(env: Env, body: dict[str, Any]) -> None:
    run_id = seed_run(env)
    response = env.client.post(f"/runs/{run_id}/answer", json=body, headers=env.headers())
    assert response.status_code == 422
    assert env.m3.posts("/answer") == []


@pytest.mark.parametrize("decision", ["maybe", "", "APPROVE", None, 1])
def test_bad_approval_decisions_are_422(env: Env, decision: Any) -> None:
    run_id = seed_run(env)
    body = {"approval_id": "a", "decision": decision}
    assert (
        env.client.post(f"/runs/{run_id}/approve", json=body, headers=env.headers()).status_code
        == 422
    )


# =========================================================================================
# GET /runs/{id}/events (SSE)
# =========================================================================================
def frames(response: Any) -> list[dict[str, Any]]:
    out = []
    for block in response.text.split("\n\n"):
        fields: dict[str, Any] = {}
        for line in block.splitlines():
            key, _, value = line.partition(": ")
            fields[key] = value
        if "data" in fields:
            fields["json"] = json.loads(fields["data"])
        if fields:
            out.append(fields)
    return out


def events_for(run_id: str, *seqs: int, last: str = "run_completed") -> list[str]:
    out = [make_event(run_id, n).model_dump_json() for n in seqs[:-1]]
    return [*out, make_event(run_id, seqs[-1], last, status="succeeded").model_dump_json()]


def test_the_stream_is_event_stream_with_ids_and_closes_after_completion() -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus(events_for(run_id, 1, 2, 3)))
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{run_id}/events", headers=e.headers())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    seen = [f for f in frames(response) if "data" in f]
    assert [(f["id"], f["event"]) for f in seen] == [
        ("1", "agent_started"),
        ("2", "agent_started"),
        ("3", "run_completed"),
    ]
    assert e.bus.channels == [run_id]
    assert e.bus.subscriptions[0].closed


def test_last_event_id_resumes_after_that_event() -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus(events_for(run_id, 4, 5)))
    with e.client:
        seed_run(e)
        e.m3.events[run_id] = [make_event(run_id, n).model_dump(mode="json") for n in (1, 2, 3)]
        headers = {**e.headers(), "Last-Event-ID": "2"}
        response = e.client.get(f"/runs/{run_id}/events", headers=headers)
    assert [f["id"] for f in frames(response) if "id" in f] == ["3", "4", "5"]
    assert ("GET", f"/runs/{run_id}/events", {"after": "2"}) in e.m3.calls


@pytest.mark.parametrize("value", ["abc", "-5", "", "1.5", "9" * 40, "2; x"])
def test_a_malformed_last_event_id_means_from_the_start(value: str) -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus(events_for(run_id, 1)))
    with e.client:
        seed_run(e)
        headers = {**e.headers(), "Last-Event-ID": value}
        response = e.client.get(f"/runs/{run_id}/events", headers=headers)
    assert response.status_code == 200
    assert [f["id"] for f in frames(response) if "id" in f] == ["1"]


def test_idle_connections_get_heartbeats() -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus([None, None, *events_for(run_id, 1)]), sse_heartbeat_s=15)
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{run_id}/events", headers=e.headers())
    beats = [f for f in frames(response) if f.get("event") == "heartbeat"]
    assert len(beats) == 2
    assert all("id" not in b for b in beats)
    assert all(t <= 15 for t in e.bus.subscriptions[0].timeouts)


def test_a_finished_run_replays_then_closes_without_waiting() -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        seed_run(e, state="succeeded")
        e.m3.events[run_id] = [
            make_event(run_id, 1).model_dump(mode="json"),
            make_event(run_id, 2, "run_completed", status="succeeded").model_dump(mode="json"),
        ]
        response = e.client.get(f"/runs/{run_id}/events", headers=e.headers())
    assert [f["event"] for f in frames(response) if "event" in f] == [
        "agent_started",
        "run_completed",
    ]


def test_a_finished_run_without_stored_events_gets_a_synthetic_closing_event() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        seed_run(e, state="failed")
        e.m3.replay_status = 404
        response = e.client.get("/runs/run_000000000001/events", headers=e.headers())
    closing = [f for f in frames(response) if f.get("event") == "run_failed"]
    assert len(closing) == 1
    assert closing[0]["json"]["payload"]["status"] == "failed"


def test_someone_elses_run_has_no_stream_just_a_json_404() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        seed_run(e, user=BOB)
        response = e.client.get("/runs/run_000000000001/events", headers=e.headers())
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert e.bus.channels == []  # never even subscribed
    assert e.m3.calls == []


def test_events_for_another_run_on_the_channel_never_leak() -> None:
    run_id = "run_000000000001"
    foreign = make_event("run_someoneelse", 1, "verdict", secret="x").model_dump_json()
    e = build(ScriptedBus([foreign, *events_for(run_id, 1)]))
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{run_id}/events", headers=e.headers())
    assert "run_someoneelse" not in response.text
    assert [f["id"] for f in frames(response) if "id" in f] == ["1"]


def test_sse_connections_are_rate_limited_per_user() -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus(events_for(run_id, 1)), sse_per_min=2)
    with e.client:
        seed_run(e)
        codes = [
            e.client.get(f"/runs/{run_id}/events", headers=e.headers()).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        other = e.client.get(f"/runs/{run_id}/events", headers=e.headers(BOB))
        assert (
            other.status_code == 404
        )  # Bob is not blocked by Alice's budget; he just has no access


def test_the_stream_response_has_the_security_headers() -> None:
    run_id = "run_000000000001"
    e = build(ScriptedBus(events_for(run_id, 1)))
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{run_id}/events", headers=e.headers())
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]


# =========================================================================================
# ideas: list, versions, one version
# =========================================================================================
def test_the_idea_list_shows_only_my_ideas_with_pagination(env: Env) -> None:
    mine = [env.m2.add_idea(ALICE, title=f"mine {i}", verdict="GO", score=80.0) for i in range(3)]
    env.m2.add_idea(BOB, title="bob's secret idea")
    first = env.client.get("/ideas?limit=2", headers=env.headers()).json()
    assert [i["idea_id"] for i in first["items"]] == mine[:2]
    assert first["next_cursor"] is not None
    second = env.client.get(
        f"/ideas?limit=2&cursor={first['next_cursor']}", headers=env.headers()
    ).json()
    assert [i["idea_id"] for i in second["items"]] == mine[2:]
    assert second["next_cursor"] is None
    item = first["items"][0]
    assert set(item) == {
        "idea_id", "title", "source", "latest_version", "latest_verdict", "score", "updated_at",
    }  # fmt: skip
    assert "bob" not in json.dumps([first, second])
    no_leaks(env.client.get("/ideas", headers=env.headers()))


def test_an_empty_idea_list_is_200(env: Env) -> None:
    assert env.client.get("/ideas", headers=env.headers()).json() == {
        "items": [],
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    ("limit", "status"), [(0, 422), (1, 200), (20, 200), (50, 200), (51, 422), (-1, 422)]
)
def test_idea_list_limit_boundaries(env: Env, limit: int, status: int) -> None:
    assert env.client.get(f"/ideas?limit={limit}", headers=env.headers()).status_code == status


@pytest.mark.parametrize("query", ["limit=abc", "limit=1.5", "limit=", "cursor=" + "x" * 201])
def test_idea_list_rejects_malformed_query(env: Env, query: str) -> None:
    assert env.client.get(f"/ideas?{query}", headers=env.headers()).status_code == 422
    assert env.m2.calls == []


def test_the_default_limit_is_20(env: Env) -> None:
    env.client.get("/ideas", headers=env.headers())
    assert env.m2.calls[-1][2]["limit"] == "20"


def test_idea_versions_list(env: Env) -> None:
    idea = env.m2.add_idea(ALICE, text="v1", verdict="PIVOT", score=60.0)
    env.m2.add_version(idea, "v2", verdict="GO", score=75.0)
    body = env.client.get(f"/ideas/{idea}/versions", headers=env.headers()).json()
    assert [(v["version"], v["text"], v["verdict"]) for v in body] == [
        (1, "v1", "PIVOT"),
        (2, "v2", "GO"),
    ]
    assert set(body[0]) == {
        "version", "text", "change_note", "parent_version", "created_at",
        "verdict", "score", "stability",
    }  # fmt: skip


def test_one_version_returns_the_stored_card_untouched(env: Env) -> None:
    idea = env.m2.add_idea(ALICE, card=CARD, verdict="GO", score=80.0)
    response = env.client.get(f"/ideas/{idea}/versions/1", headers=env.headers())
    assert response.status_code == 200
    body = response.json()
    assert body["card"] == CARD  # the gateway never edits a verdict
    assert body["idea_id"] == idea
    assert "internal_only" not in body and "verdict_payload" not in body


def test_a_version_without_a_verdict_has_a_null_card(env: Env) -> None:
    idea = env.m2.add_idea(ALICE)
    assert env.client.get(f"/ideas/{idea}/versions/1", headers=env.headers()).json()["card"] is None


@pytest.mark.parametrize("path", ["versions", "versions/1"])
def test_someone_elses_idea_is_a_404_for_history_and_versions(env: Env, path: str) -> None:
    idea = env.m2.add_idea(BOB, card=CARD, verdict="GO", score=80.0)
    other = env.client.get(f"/ideas/{idea}/{path}", headers=env.headers())
    missing = env.client.get(f"/ideas/{uuid.uuid4()}/{path}", headers=env.headers())
    assert other.status_code == missing.status_code == 404
    assert other.json()["error"]["message"] == missing.json()["error"]["message"]
    assert "GO" not in other.text


@pytest.mark.parametrize(
    ("version", "status"),
    [(0, 422), (1, 200), (2, 404), (999, 404), (-1, 422), ("x", 422), ("1.5", 422)],
)
def test_version_number_boundaries(env: Env, version: Any, status: int) -> None:
    idea = env.m2.add_idea(ALICE)
    assert (
        env.client.get(f"/ideas/{idea}/versions/{version}", headers=env.headers()).status_code
        == status
    )


@pytest.mark.parametrize(
    "idea_id", ["abc", "123", "..", "%2e%2e", "g" * 36, str(uuid.uuid4()) + "x"]
)
def test_idea_ids_must_be_uuids(env: Env, idea_id: str) -> None:
    for path in (f"/ideas/{idea_id}/versions", f"/ideas/{idea_id}/versions/1"):
        assert env.client.get(path, headers=env.headers()).status_code in (404, 422)
    assert env.m2.calls == []


# =========================================================================================
# compare
# =========================================================================================
def two_ideas(env: Env, user: str = ALICE) -> tuple[str, str]:
    a = env.m2.add_idea(
        user,
        title="A",
        card={"scores": {"demand_pain": 4.0, "competition": 2.0}},
        verdict="GO",
        score=80.0,
    )
    b = env.m2.add_idea(
        user,
        title="B",
        card={"scores": {"demand_pain": 3.0, "competition": None}},
        verdict="PIVOT",
        score=55.0,
    )
    return a, b


def compare(env: Env, ids: str, user: str = ALICE) -> Any:
    return env.client.get(f"/compare?ids={ids}", headers=env.headers(user))


def test_compare_two_ideas_returns_rows_dimensions_and_verdicts_copied_from_m2(env: Env) -> None:
    a, b = two_ideas(env)
    response = compare(env, f"{a},{b}")
    assert response.status_code == 200
    body = response.json()
    assert [r["idea_id"] for r in body["ideas"]] == [a, b]
    assert body["verdicts"] == {a: "GO", b: "PIVOT"}
    by_dim = {d["dimension"]: d for d in body["dimensions"]}
    assert by_dim["demand_pain"] == {
        "dimension": "demand_pain",
        "scores": {a: 4.0, b: 3.0},
        "delta": 1.0,
    }
    assert by_dim["competition"]["delta"] is None  # one score is unknown: no difference to show
    assert env.m2.calls[-1][0:2] == ("GET", "/internal/compare")


def test_compare_keeps_the_requested_order(env: Env) -> None:
    a, b = two_ideas(env)
    assert [r["idea_id"] for r in compare(env, f"{b},{a}").json()["ideas"]] == [b, a]


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_compare_accepts_two_to_five_ideas(env: Env, count: int) -> None:
    ids = [env.m2.add_idea(ALICE, verdict="GO", score=70.0) for _ in range(count)]
    response = compare(env, ",".join(ids))
    assert response.status_code == 200
    assert len(response.json()["ideas"]) == count


def test_compare_with_one_or_six_ideas_is_422(env: Env) -> None:
    ids = [env.m2.add_idea(ALICE) for _ in range(6)]
    assert compare(env, ids[0]).status_code == 422
    assert compare(env, ",".join(ids)).status_code == 422
    assert env.m2.calls == []


@pytest.mark.parametrize(
    "ids",
    [
        "",
        ",",
        "a,b",
        "not-a-uuid,also-not",
        f"{IDEA},",
        f",{IDEA}",
        f"{IDEA},{IDEA}",
        f"{IDEA};{uuid.uuid4()}",
    ],
)
def test_malformed_compare_ids_are_422(env: Env, ids: str) -> None:
    assert compare(env, ids).status_code == 422
    assert env.m2.calls == []


def test_compare_without_ids_is_422(env: Env) -> None:
    assert env.client.get("/compare", headers=env.headers()).status_code == 422


def test_comparing_with_someone_elses_idea_is_a_404_that_reveals_nothing(env: Env) -> None:
    mine, _ = two_ideas(env)
    theirs, _ = two_ideas(env, user=BOB)
    missing = compare(env, f"{mine},{uuid.uuid4()}")
    foreign = compare(env, f"{mine},{theirs}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert "PIVOT" not in foreign.text and "GO" not in foreign.text


def test_a_short_answer_from_m2_is_a_502(env: Env) -> None:
    a, b = two_ideas(env)
    original = env.m2.__class__._compare

    def short(self: FakeM2, request: Any) -> Any:
        response = original(self, request)
        return type(response)(200, json=response.json()[:1])

    env.m2.__class__._compare = short  # type: ignore[method-assign]
    try:
        assert compare(env, f"{a},{b}").status_code == 502
    finally:
        env.m2.__class__._compare = original  # type: ignore[method-assign]


# =========================================================================================
# challenge and what-if
# =========================================================================================
def seeded_idea(env: Env, user: str = ALICE) -> str:
    return env.m2.add_idea(user, card=CARD, verdict="GO", score=80.0)


def test_challenge_starts_a_challenge_run_for_the_owner(env: Env) -> None:
    idea = seeded_idea(env)
    response = env.client.post(
        f"/ideas/{idea}/challenge", json={"version": 1, "claim_id": "c1"}, headers=env.headers()
    )
    assert response.status_code == 202
    assert set(response.json()) == {"run_id"}
    assert env.m3.posts("/runs")[0][2] == {
        "kind": "challenge",
        "user_id": ALICE,
        "input": {"idea_id": idea, "version": 1, "claim_id": "c1"},
    }
    assert env.m2.runs[response.json()["run_id"]]["kind"] == "challenge"


def test_challenging_a_claim_that_does_not_exist_is_404(env: Env) -> None:
    idea = seeded_idea(env)
    response = env.client.post(
        f"/ideas/{idea}/challenge", json={"version": 1, "claim_id": "nope"}, headers=env.headers()
    )
    assert response.status_code == 404
    assert env.m3.calls == []


def test_challenging_a_version_without_a_verdict_is_409(env: Env) -> None:
    idea = env.m2.add_idea(ALICE)
    response = env.client.post(
        f"/ideas/{idea}/challenge", json={"version": 1, "claim_id": "c1"}, headers=env.headers()
    )
    assert response.status_code == 409
    assert env.m3.calls == []


def test_whatif_with_a_proposed_change(env: Env) -> None:
    idea = seeded_idea(env)
    response = env.client.post(
        f"/ideas/{idea}/whatif", json={"version": 1, "change_id": "ch_1"}, headers=env.headers()
    )
    assert response.status_code == 202
    assert env.m3.posts("/runs")[0][2] == {
        "kind": "whatif",
        "user_id": ALICE,
        "input": {"idea_id": idea, "version": 1, "change_id": "ch_1"},
    }


def test_whatif_with_a_custom_change(env: Env) -> None:
    idea = seeded_idea(env)
    response = env.client.post(
        f"/ideas/{idea}/whatif",
        json={"version": 1, "custom_change": "Add a third teammate"},
        headers=env.headers(),
    )
    assert response.status_code == 202
    assert env.m3.posts("/runs")[0][2]["input"] == {
        "idea_id": idea,
        "version": 1,
        "custom_change": "Add a third teammate",
    }


@pytest.mark.parametrize("action", ["challenge", "whatif"])
def test_challenge_and_whatif_on_someone_elses_idea_are_404_and_start_nothing(
    env: Env, action: str
) -> None:
    idea = seeded_idea(env, user=BOB)
    body = (
        {"version": 1, "claim_id": "c1"}
        if action == "challenge"
        else {"version": 1, "change_id": "c"}
    )
    response = env.client.post(f"/ideas/{idea}/{action}", json=body, headers=env.headers())
    missing = env.client.post(f"/ideas/{uuid.uuid4()}/{action}", json=body, headers=env.headers())
    assert response.status_code == missing.status_code == 404
    assert response.json()["error"]["message"] == missing.json()["error"]["message"]
    assert env.m3.calls == []
    assert env.m2.runs == {}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("challenge", {"version": 0, "claim_id": "c1"}),
        ("challenge", {"version": 1}),
        ("challenge", {"claim_id": "c1"}),
        ("challenge", {"version": "1", "claim_id": "c1"}),
        ("challenge", {"version": 1, "claim_id": "c1", "user_id": BOB}),
        ("challenge", {"version": 1, "claim_id": "a b"}),
        ("whatif", {"version": 1}),
        ("whatif", {"version": 1, "change_id": "c", "custom_change": "x"}),
        ("whatif", {"version": 1, "custom_change": "x" * 1001}),
        ("whatif", {"version": 1, "custom_change": "  "}),
        ("whatif", {"version": 0, "change_id": "c"}),
        ("whatif", {"change_id": "c"}),
    ],
)
def test_bad_challenge_and_whatif_bodies_are_422(env: Env, path: str, body: dict[str, Any]) -> None:
    idea = seeded_idea(env)
    assert (
        env.client.post(f"/ideas/{idea}/{path}", json=body, headers=env.headers()).status_code
        == 422
    )
    assert env.m3.calls == []


def test_whatif_custom_change_boundary_1000_is_accepted(env: Env) -> None:
    idea = seeded_idea(env)
    body = {"version": 1, "custom_change": "c" * 1000}
    assert (
        env.client.post(f"/ideas/{idea}/whatif", json=body, headers=env.headers()).status_code
        == 202
    )


def test_whatif_on_a_missing_version_is_404(env: Env) -> None:
    idea = seeded_idea(env)
    response = env.client.post(
        f"/ideas/{idea}/whatif", json={"version": 7, "change_id": "c"}, headers=env.headers()
    )
    assert response.status_code == 404


# =========================================================================================
# cross-user matrix: Bob owns everything, Alice asks for it all, always 404
# =========================================================================================
def test_cross_user_matrix_every_endpoint_answers_404_and_changes_nothing(env: Env) -> None:
    idea = seeded_idea(env, user=BOB)
    other = seeded_idea(env, user=BOB)
    run_id = seed_run(env, user=BOB)
    attempts: list[tuple[str, str, Any]] = [
        ("GET", f"/runs/{run_id}", None),
        ("POST", f"/runs/{run_id}/answer", {"question_id": "q", "answer": 1}),
        ("POST", f"/runs/{run_id}/approve", {"approval_id": "a", "decision": "approve"}),
        ("GET", f"/runs/{run_id}/events", None),
        ("GET", f"/runs/{run_id}/trace", None),
        ("GET", f"/ideas/{idea}/versions", None),
        ("GET", f"/ideas/{idea}/versions/1", None),
        ("GET", f"/compare?ids={idea},{other}", None),
        ("POST", f"/ideas/{idea}/challenge", {"version": 1, "claim_id": "c1"}),
        ("POST", f"/ideas/{idea}/whatif", {"version": 1, "change_id": "c"}),
        ("POST", "/validate", {"idea_text": "hijack", "title": "t", "idea_id": idea}),
    ]
    before_m3 = len(env.m3.calls)
    for method, path, body in attempts:
        response = call(env, method, path, body, env.headers(ALICE))
        assert response.status_code == 404, (method, path, response.text)
        assert error_code(response) == "NOT_FOUND"
        no_leaks(response)
    assert len(env.m3.calls) == before_m3  # no read and no write ever reached M3
    assert len(env.m2.versions[idea]) == 1
    assert env.client.get("/ideas", headers=env.headers(ALICE)).json()["items"] == []
    assert len(env.client.get("/ideas", headers=env.headers(BOB)).json()["items"]) == 2


# =========================================================================================
# readiness and the error envelope
# =========================================================================================
def test_ready_checks_the_orchestrator(env: Env) -> None:
    assert env.client.get("/ready").json()["checks"]["orchestrator"] is True
    env.m3.ready = False
    response = env.client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["orchestrator"] is False


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_errors_use_the_standard_envelope(env: Env, method: str, path: str, body: Any) -> None:
    response = call(env, method, path, body, {})
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message", "details", "request_id"}
    assert response.json()["error"]["request_id"]


def test_unknown_routes_and_wrong_methods_do_not_leak(env: Env) -> None:
    for method, path in (
        ("GET", "/validate"),
        ("DELETE", "/ideas"),
        ("GET", "/admin"),
        ("POST", "/runs"),
    ):
        response = env.client.request(method, path, headers=env.headers())
        assert response.status_code in (404, 405)
        no_leaks(response)


def test_whatif_on_a_version_without_a_verdict_is_409(env: Env) -> None:
    idea = env.m2.add_idea(ALICE)
    response = env.client.post(
        f"/ideas/{idea}/whatif", json={"version": 1, "change_id": "c"}, headers=env.headers()
    )
    assert response.status_code == 409
    assert env.m3.calls == []


def test_the_real_wiring_builds_its_clients_from_the_environment() -> None:
    settings = Settings(
        jwt_secret=SecretStr(SECRET),
        internal_token=SecretStr("t"),
        profile_url="http://127.0.0.1:1",
        orchestrator_url="http://127.0.0.1:1",
        redis_url=SecretStr("redis://127.0.0.1:1"),
        upstream_timeout_s=0.2,
    )
    headers = {"Authorization": f"Bearer {issue_token(SECRET, ALICE, 3600)}"}
    with TestClient(build_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/ready").status_code == 503
        assert client.get("/ready").json()["checks"] == {
            "redis": False,
            "profile": False,
            "orchestrator": False,
        }
        assert client.get("/ideas", headers=headers).status_code == 503  # limiter fails closed


PARTIAL = {
    "verdict": None,
    "partial": True,
    "needs_input": ["estimated_effort_hours"],
    "scores": {"demand_pain": 3.5},
    "notes": ["effort unknown"],
}


def test_a_partial_card_is_forwarded_honestly_with_a_null_verdict(env: Env) -> None:
    run_id = seed_run(env, state="succeeded", result=PARTIAL)
    body = env.client.get(f"/runs/{run_id}", headers=env.headers()).json()
    assert body["state"] == "succeeded"
    assert body["result"] == PARTIAL  # verbatim: no verdict or score invented or filled in
    assert body["result"]["verdict"] is None
    assert "score" not in body["result"]


def test_question_events_keep_their_field_and_range_for_the_form() -> None:
    run_id = "run_000000000001"
    question = make_event(
        run_id,
        1,
        "question",
        question_id="q1",
        field="estimated_effort_hours",
        required=True,
        range=[0, 10000],
    ).model_dump_json()
    e = build(ScriptedBus([question, *events_for(run_id, 2)]))
    with e.client:
        seed_run(e)
        response = e.client.get(f"/runs/{run_id}/events", headers=e.headers())
    asked = next(f for f in frames(response) if f.get("event") == "question")
    assert asked["json"]["payload"]["range"] == [0, 10000]
    assert asked["json"]["payload"]["required"] is True


# =========================================================================================
# review follow-ups: ids belong to the run, and a stream never fails open
# =========================================================================================
RUN = "run_000000000001"


@pytest.mark.parametrize(
    "approval_id",
    [
        "a1",
        "ap_",
        "ap_run_000000000002_deep_search",  # another run's approval
        "xap_run_000000000001_deep_search",
        "AP_run_000000000001_deep_search",
        "",
    ],
)
def test_an_approval_id_must_belong_to_this_run(env: Env, approval_id: str) -> None:
    seed_run(env)
    r = env.client.post(
        f"/runs/{RUN}/approve",
        json={"approval_id": approval_id, "decision": "approve"},
        headers=env.headers(),
    )
    assert r.status_code == 422
    assert env.m3.posts("/approve") == []  # never forwarded


@pytest.mark.parametrize("question_id", ["x_effort", "Q_effort", "q", "effort", "q-effort", "q_"])
def test_a_question_id_must_look_like_one(env: Env, question_id: str) -> None:
    seed_run(env)
    r = env.client.post(
        f"/runs/{RUN}/answer", json={"question_id": question_id, "answer": 5}, headers=env.headers()
    )
    assert r.status_code == 422
    assert env.m3.posts("/answer") == []


def test_ids_of_the_right_shape_are_forwarded_unchanged(env: Env) -> None:
    seed_run(env)
    a = env.client.post(
        f"/runs/{RUN}/approve",
        json={"approval_id": f"ap_{RUN}_deep_search", "decision": "approve"},
        headers=env.headers(),
    )
    q = env.client.post(
        f"/runs/{RUN}/answer", json={"question_id": "q_hours", "answer": 5}, headers=env.headers()
    )
    assert (a.status_code, q.status_code) == (202, 202)


def test_the_ownership_404_comes_before_any_id_check(env: Env) -> None:
    seed_run(env, user=BOB)
    r = env.client.post(
        f"/runs/{RUN}/approve", json={"approval_id": "garbage", "decision": "approve"},
        headers=env.headers(),
    )  # fmt: skip
    assert r.status_code == 404  # not 422: nothing about someone else's run is revealed


def test_the_event_stream_is_a_503_when_m3_is_unreachable_not_a_silent_200() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        seed_run(e)
        e.m3.fail = 500
        r = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/json")
    assert "secret internal detail" not in r.text


def test_the_event_stream_is_a_503_when_redis_is_unreachable() -> None:
    class DeadBus:
        async def subscribe(self, run_id: str) -> Any:
            raise OSError("redis down")

    e = build(DeadBus())  # type: ignore[arg-type]
    with e.client:
        seed_run(e)
        r = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
    assert r.status_code == 503
    assert "redis" not in r.text.lower()


def test_a_failed_stream_open_releases_its_slot() -> None:
    e = build(ScriptedBus(events_for(RUN, 1)), sse_streams_per_user=1)
    with e.client:
        seed_run(e)
        e.m3.fail = 500
        for _ in range(3):  # would exhaust a single slot if each failure leaked it
            assert e.client.get(f"/runs/{RUN}/events", headers=e.headers()).status_code == 503
        e.m3.fail = None
        ok = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
    assert ok.status_code == 200


# =========================================================================================
# M3 restarted and forgot a run that M2 still calls running (restart recovery, fail cleanly)
# =========================================================================================
def forgotten_run(e: Env, state: str) -> str:
    e.m2.add_run(RUN, ALICE)
    e.m2.runs[RUN]["state"] = state  # M3 has no entry for it: it was lost in a restart
    return RUN


@pytest.mark.parametrize("state", ["created", "running", "awaiting_input", "awaiting_approval"])
def test_a_run_m3_forgot_is_reported_failed_and_the_mirror_is_corrected(state: str) -> None:
    e = build()
    with e.client:
        forgotten_run(e, state)
        view = e.client.get(f"/runs/{RUN}", headers=e.headers()).json()
        assert view["state"] == "failed"
        assert e.m2.runs[RUN]["state"] == "failed"  # next read does not depend on M3 at all


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled", "budget_stopped"])
def test_a_finished_run_m3_forgot_keeps_its_final_state(state: str) -> None:
    e = build()
    with e.client:
        forgotten_run(e, state)
        view = e.client.get(f"/runs/{RUN}", headers=e.headers()).json()
        assert view["state"] == state


def test_a_forgotten_run_is_still_owner_only() -> None:
    e = build()
    with e.client:
        e.m2.add_run(RUN, BOB)
        assert e.client.get(f"/runs/{RUN}", headers=e.headers()).status_code == 404


def test_a_cancelled_run_m3_forgot_closes_its_stream_instead_of_crashing() -> None:
    e = build(ScriptedBus(then_idle=True))
    with e.client:
        forgotten_run(e, "cancelled")
        e.m3.replay_status = 404
        response = e.client.get(f"/runs/{RUN}/events", headers=e.headers())
    closing = [f for f in frames(response) if f.get("event") == "run_failed"]
    assert len(closing) == 1
    assert closing[0]["json"]["payload"]["status"] == "cancelled"
