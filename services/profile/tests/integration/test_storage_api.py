import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from common.cache_keys import verdict_cache_key
from common.schemas.types import DIMENSIONS
from svc_profile.config import Settings
from svc_profile.main import build_app

pytestmark = pytest.mark.integration
H = {"X-Internal-Token": "tok"}


@pytest.fixture
def client(migrated_url: str, redis_url: str, engine) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    Redis.from_url(redis_url).flushdb()
    settings = Settings(database_url=migrated_url, redis_url=redis_url, internal_token="tok")
    with TestClient(build_app(settings)) as test_client:
        yield test_client


def user(client: TestClient, email: str = "ada@example.com") -> str:
    r = client.post(
        "/internal/users", json={"email": email, "password": "correct horse"}, headers=H
    )
    return str(r.json()["user_id"])


def idea(
    client: TestClient, user_id: str, title: str = "Notes tool", text: str = "export notes"
) -> str:
    body = {"user_id": user_id, "title": title, "text": text}
    r = client.post("/internal/ideas", json=body, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1
    return str(r.json()["idea_id"])


def verdict_payload(
    idea_id: str, version: int = 1, label: str = "GO", score: float = 70.5
) -> dict[str, Any]:
    scores = [
        {
            "dimension": d,
            "score": 3.5,
            "confidence": 0.8,
            "evidence_ids": ["card_8f21ab00#c2"],
            "reason": "cites the evidence",
            "rubric_version": f"{d}@v1",
            "model": "scorer@v1",
            "adjusted_by_challenge": 0.0,
        }
        for d in DIMENSIONS
    ]
    return {
        "idea_id": idea_id,
        "version": version,
        "run_id": "run_1",
        "verdict": label,
        "stability": "BORDERLINE",
        "score": score,
        "confidence": 0.825,
        "gates": {
            "evidence": "pass",
            "feasibility": "pass",
            "risk": "pass",
            "duplicate": "pass",
        },
        "weights": dict.fromkeys(DIMENSIONS, 16.0),
        "thresholds": {"T_go": 70, "T_no": 50, "floor": 1.5},
        "scores": scores,
        "weakest_dimension": None,
        "what_would_make_go": [],
        "sensitivity": {"runs": 14, "flipped": 4},
        "versions": {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"},
    }


def store(client: TestClient, user_id: str, idea_id: str, version: int = 1, **kw: Any) -> Any:
    body = {
        "user_id": user_id,
        "idea_id": idea_id,
        "version": version,
        "profile_version": 1,
        "verdict": verdict_payload(idea_id, version, **kw),
    }
    return client.post("/internal/verdicts", json=body, headers=H)


# ---- ideas and versions
def test_a_new_idea_starts_at_version_one_and_each_change_adds_a_version(
    client: TestClient,
) -> None:
    uid = user(client)
    iid = idea(client, uid)
    v2 = client.post(
        f"/internal/ideas/{iid}/versions",
        json={"user_id": uid, "text": "narrower", "change_note": "what-if", "parent_version": 1},
        headers=H,
    )
    assert v2.json() == {"idea_id": iid, "version": 2}
    listing = client.get(f"/internal/ideas/{iid}/versions?user_id={uid}", headers=H).json()
    assert [(v["version"], v["change_note"], v["parent_version"]) for v in listing] == [
        (1, None, None),
        (2, "what-if", 1),
    ]


def test_a_parent_version_must_already_exist(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    body = {"user_id": uid, "text": "x", "parent_version": 5}
    assert client.post(f"/internal/ideas/{iid}/versions", json=body, headers=H).status_code == 422


def test_someone_elses_idea_looks_like_it_does_not_exist(client: TestClient) -> None:
    owner, other = user(client), user(client, "eve@example.com")
    iid = idea(client, owner)
    for path in (
        f"/internal/ideas/{iid}/versions?user_id={other}",
        f"/internal/ideas/{iid}/versions/1?user_id={other}",
    ):
        assert client.get(path, headers=H).status_code == 404
    body = {"user_id": other, "text": "hijack"}
    assert client.post(f"/internal/ideas/{iid}/versions", json=body, headers=H).status_code == 404
    assert store(client, other, iid).status_code == 404


def test_unknown_user_and_unknown_idea_are_404(client: TestClient) -> None:
    ghost = str(uuid.uuid4())
    body = {"user_id": ghost, "title": "t", "text": "x"}
    assert client.post("/internal/ideas", json=body, headers=H).status_code == 404
    uid = user(client)
    assert (
        client.get(f"/internal/ideas/{ghost}/versions?user_id={uid}", headers=H).status_code == 404
    )


def test_idea_input_limits(client: TestClient) -> None:
    uid = user(client)
    too_long = {"user_id": uid, "title": "t", "text": "x" * 4001}
    empty = {"user_id": uid, "title": "", "text": "x"}
    for body in (too_long, empty):
        assert client.post("/internal/ideas", json=body, headers=H).status_code == 422


def test_listing_is_newest_first_and_paginates_without_repeats(client: TestClient) -> None:
    uid = user(client)
    ids = [idea(client, uid, title=f"idea {i}") for i in range(5)]
    seen: list[str] = []
    cursor = None
    for _ in range(3):
        url = f"/internal/ideas?user_id={uid}&limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=H).json()
        seen += [i["idea_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == list(reversed(ids))
    assert cursor is None


def test_an_invalid_cursor_is_422_and_pages_only_show_the_owners_ideas(client: TestClient) -> None:
    owner, other = user(client), user(client, "eve@example.com")
    idea(client, owner)
    assert (
        client.get(f"/internal/ideas?user_id={owner}&cursor=not-a-cursor", headers=H).status_code
        == 422
    )
    assert client.get(f"/internal/ideas?user_id={other}", headers=H).json()["items"] == []


# ---- verdicts
def test_a_stored_verdict_shows_up_in_the_idea_history(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    assert store(client, uid, iid).status_code == 201
    versions = client.get(f"/internal/ideas/{iid}/versions?user_id={uid}", headers=H).json()
    assert (versions[0]["verdict"], versions[0]["score"], versions[0]["stability"]) == (
        "GO",
        70.5,
        "BORDERLINE",
    )
    detail = client.get(f"/internal/ideas/{iid}/versions/1?user_id={uid}", headers=H).json()
    assert detail["verdict_payload"]["gates"]["evidence"] == "pass"
    assert detail["title"] == "Notes tool"
    summary = client.get(f"/internal/ideas?user_id={uid}", headers=H).json()["items"][0]
    assert (summary["latest_version"], summary["latest_verdict"], summary["score"]) == (
        1,
        "GO",
        70.5,
    )


def test_the_six_dimension_scores_are_stored_per_verdict(client: TestClient, engine) -> None:  # type: ignore[no-untyped-def]
    uid = user(client)
    iid = idea(client, uid)
    store(client, uid, iid)
    rows = client.get(f"/internal/compare?user_id={uid}&idea_ids={iid}&idea_ids={iid}", headers=H)
    assert rows.status_code == 200
    assert set(rows.json()[0]["scores"]) == set(DIMENSIONS)


def test_a_verdict_must_match_its_idea_and_version(client: TestClient) -> None:
    uid = user(client)
    iid, other = idea(client, uid), idea(client, uid, title="other")
    body = {
        "user_id": uid,
        "idea_id": iid,
        "version": 1,
        "profile_version": 1,
        "verdict": verdict_payload(other),
    }
    assert client.post("/internal/verdicts", json=body, headers=H).status_code == 422
    assert store(client, uid, iid, version=9).status_code in {404, 422}


def test_a_bad_verdict_payload_is_rejected(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    bad = {
        "user_id": uid,
        "idea_id": iid,
        "version": 1,
        "profile_version": 1,
        "verdict": {**verdict_payload(iid), "verdict": "MAYBE"},
    }
    assert client.post("/internal/verdicts", json=bad, headers=H).status_code == 422


def test_the_latest_verdict_of_a_version_wins(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    store(client, uid, iid, label="PIVOT", score=60.0)
    store(client, uid, iid, label="GO", score=72.0)
    detail = client.get(f"/internal/ideas/{iid}/versions/1?user_id={uid}", headers=H).json()
    assert (detail["verdict"], detail["score"]) == ("GO", 72.0)


# ---- compare
def test_compare_returns_one_row_per_idea_with_scores(client: TestClient) -> None:
    uid = user(client)
    a, b = idea(client, uid, title="A"), idea(client, uid, title="B")
    store(client, uid, a, score=71.0)
    store(client, uid, b, label="PIVOT", score=55.0)
    rows = client.get(
        f"/internal/compare?user_id={uid}&idea_ids={a}&idea_ids={b}", headers=H
    ).json()
    assert [(r["title"], r["verdict"], r["score"]) for r in rows] == [
        ("A", "GO", 71.0),
        ("B", "PIVOT", 55.0),
    ]


def test_compare_needs_two_to_five_of_my_own_ideas(client: TestClient) -> None:
    owner, other = user(client), user(client, "eve@example.com")
    a, b = idea(client, owner), idea(client, owner)
    assert (
        client.get(f"/internal/compare?user_id={owner}&idea_ids={a}", headers=H).status_code == 422
    )
    leaked = client.get(f"/internal/compare?user_id={other}&idea_ids={a}&idea_ids={b}", headers=H)
    assert leaked.status_code == 404


# ---- runs and approvals
def run_body(uid: str, state: str = "RUNNING") -> dict[str, Any]:
    return {"user_id": uid, "kind": "validate", "state": state, "input": {"idea": "x"}}


def test_a_run_can_be_created_updated_and_read_back_by_its_owner(client: TestClient) -> None:
    uid = user(client)
    created = client.put("/internal/runs/run_1", json=run_body(uid), headers=H)
    assert created.status_code == 200
    updated = client.put(
        "/internal/runs/run_1",
        json={**run_body(uid, "AWAITING_INPUT"), "pending_question": {"q": "hours?"}},
        headers=H,
    ).json()
    assert (updated["state"], updated["pending_question"]) == ("AWAITING_INPUT", {"q": "hours?"})
    assert updated["updated_at"] >= created.json()["updated_at"]
    got = client.get(f"/internal/runs/run_1?user_id={uid}", headers=H).json()
    assert got["state"] == "AWAITING_INPUT"


def test_runs_are_private_to_their_user(client: TestClient) -> None:
    owner, other = user(client), user(client, "eve@example.com")
    client.put("/internal/runs/run_1", json=run_body(owner), headers=H)
    assert client.get(f"/internal/runs/run_1?user_id={other}", headers=H).status_code == 404
    assert client.put("/internal/runs/run_1", json=run_body(other), headers=H).status_code == 404
    ghost = run_body(str(uuid.uuid4()))
    assert client.put("/internal/runs/run_2", json=ghost, headers=H).status_code == 404


def test_run_kind_is_validated(client: TestClient) -> None:
    uid = user(client)
    bad = {**run_body(uid), "kind": "wander"}
    assert client.put("/internal/runs/run_1", json=bad, headers=H).status_code == 422


def _put_runs(client: TestClient, uid: str, states: dict[str, str]) -> None:
    for run_id, state in states.items():
        assert client.put(
            f"/internal/runs/{run_id}", json=run_body(uid, state), headers=H
        ).is_success


def _listed(client: TestClient, query: str) -> list[str]:
    r = client.get(f"/internal/runs?{query}", headers=H)
    assert r.status_code == 200, r.text
    return [row["run_id"] for row in r.json()]


def test_runs_can_be_listed_by_state_for_recovery(client: TestClient) -> None:
    a, b = user(client), user(client, "bo@example.com")
    _put_runs(client, a, {"run_a": "running", "run_b": "awaiting_input", "run_c": "succeeded"})
    _put_runs(client, b, {"run_d": "awaiting_approval", "run_e": "failed"})
    assert _listed(client, "state=running") == ["run_a"]
    both = _listed(client, "state=running,awaiting_input,awaiting_approval")
    assert both == ["run_a", "run_b", "run_d"]  # every user, ordered by run id
    assert _listed(client, "state=nothing_like_this") == []


def test_the_run_list_pages_by_run_id(client: TestClient) -> None:
    uid = user(client)
    _put_runs(client, uid, {f"run_{n:03d}": "running" for n in range(7)})
    assert _listed(client, "state=running&limit=3") == ["run_000", "run_001", "run_002"]
    assert _listed(client, "state=running&limit=3&after=run_002") == [
        "run_003",
        "run_004",
        "run_005",
    ]
    assert _listed(client, "state=running&limit=3&after=run_005") == ["run_006"]
    assert _listed(client, "state=running&after=run_006") == []


def test_a_listed_run_carries_what_recovery_needs(client: TestClient) -> None:
    uid = user(client)
    body = {**run_body(uid, "awaiting_input"), "context": {"step": "WAIT_ANSWER"}}
    client.put("/internal/runs/run_1", json=body, headers=H)
    [row] = client.get("/internal/runs?state=awaiting_input", headers=H).json()
    assert (row["user_id"], row["kind"], row["input"]) == (uid, "validate", {"idea": "x"})
    assert row["context"] == {"step": "WAIT_ANSWER"}


@pytest.mark.parametrize(
    "query",
    [
        "",  # a state filter is mandatory: no unbounded dump of every run
        "state=",
        "state=running&limit=0",
        "state=running&limit=501",
        "state=running&limit=-1",
        "state=running&limit=abc",
        "state=Bad-State",
        "state=" + ",".join(f"s{n}" for n in range(11)),  # more than 10 states
        "state=running&after=" + "x" * 81,
    ],
)
def test_bad_run_list_queries_are_rejected(client: TestClient, query: str) -> None:
    assert client.get(f"/internal/runs?{query}", headers=H).status_code == 422


def test_the_run_list_needs_the_internal_token(client: TestClient) -> None:
    assert client.get("/internal/runs?state=running").status_code in (401, 403)


def test_the_run_list_edge_limits_are_accepted(client: TestClient) -> None:
    uid = user(client)
    _put_runs(client, uid, {"run_1": "running"})
    assert _listed(client, "state=running&limit=1") == ["run_1"]
    assert _listed(client, "state=running&limit=500") == ["run_1"]


def test_fifty_runs_list_in_pages_without_loss_or_repeat(client: TestClient) -> None:
    uid = user(client)
    _put_runs(client, uid, {f"run_{n:03d}": "awaiting_input" for n in range(50)})
    seen: list[str] = []
    after = ""
    while page := _listed(client, f"state=awaiting_input&limit=20&after={after}"):
        seen += page
        after = page[-1]
    assert seen == [f"run_{n:03d}" for n in range(50)]


def test_an_oversized_run_row_is_refused(client: TestClient) -> None:
    uid = user(client)
    big = {"x": "a" * 70_000}
    assert (
        client.put(
            "/internal/runs/run_1", json={**run_body(uid), "context": big}, headers=H
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/internal/runs/run_1", json={**run_body(uid), "input": big}, headers=H
        ).status_code
        == 422
    )
    assert client.put("/internal/runs/run_1", json=run_body(uid), headers=H).status_code == 200


def test_an_approval_is_decided_exactly_once(client: TestClient) -> None:
    body = {
        "run_id": "run_1",
        "action": "deep_search",
        "estimated_cost_usd": 0.03,
        "budget_usd": 0.05,
    }
    created = client.post("/internal/approvals", json=body, headers=H)
    assert created.status_code == 201
    assert created.json()["decision"] == "pending"
    aid = created.json()["id"]
    done = client.put(f"/internal/approvals/{aid}", json={"decision": "approved"}, headers=H)
    assert (done.status_code, done.json()["decision"]) == (200, "approved")
    assert done.json()["decided_at"] is not None
    again = client.put(f"/internal/approvals/{aid}", json={"decision": "declined"}, headers=H)
    assert again.status_code == 409
    missing = client.put(
        f"/internal/approvals/{uuid.uuid4()}", json={"decision": "approved"}, headers=H
    )
    assert missing.status_code == 404


def test_approval_inputs_are_validated(client: TestClient) -> None:
    bad = {"run_id": "r", "action": "x", "estimated_cost_usd": -1, "budget_usd": 1}
    assert client.post("/internal/approvals", json=bad, headers=H).status_code == 422
    aid = client.post(
        "/internal/approvals",
        json={"run_id": "r", "action": "x", "estimated_cost_usd": 1, "budget_usd": 1},
        headers=H,
    ).json()["id"]
    assert (
        client.put(
            f"/internal/approvals/{aid}", json={"decision": "pending"}, headers=H
        ).status_code
        == 422
    )


# ---- cache
KEY = dict(
    idea_id="i1",
    idea_version=1,
    idea_text="export notes",
    profile_version=1,
    rubrics_version="r1",
    weights_version="w1",
    engine_version="m11@v1",
)


def test_cache_miss_then_hit_with_the_stored_value(client: TestClient) -> None:
    key = verdict_cache_key(**KEY)  # type: ignore[arg-type]
    assert client.get(f"/internal/cache/verdict/{key}", headers=H).json() == {
        "hit": False,
        "value": None,
    }
    put = client.put(f"/internal/cache/verdict/{key}", json={"value": {"verdict": "GO"}}, headers=H)
    assert put.status_code == 204
    hit = client.get(f"/internal/cache/verdict/{key}", headers=H).json()
    assert hit == {"hit": True, "value": {"verdict": "GO"}}


def test_changing_any_version_is_a_cache_miss(client: TestClient) -> None:
    key = verdict_cache_key(**KEY)  # type: ignore[arg-type]
    client.put(f"/internal/cache/verdict/{key}", json={"value": {"verdict": "GO"}}, headers=H)
    for field, changed in [
        ("profile_version", 2),
        ("rubrics_version", "r2"),
        ("weights_version", "w2"),
        ("engine_version", "m11@v2"),
        ("idea_version", 2),
        ("idea_text", "something else"),
    ]:
        other = verdict_cache_key(**{**KEY, field: changed})  # type: ignore[arg-type]
        assert client.get(f"/internal/cache/verdict/{other}", headers=H).json()["hit"] is False, (
            field
        )


def test_every_cached_entry_has_a_ttl(client: TestClient, redis_url: str) -> None:
    key = verdict_cache_key(**KEY)  # type: ignore[arg-type]
    client.put(f"/internal/cache/verdict/{key}", json={"value": {"a": 1}}, headers=H)
    client.put("/internal/cache/verdict/short", json={"value": {"a": 1}, "ttl_s": 30}, headers=H)
    redis = Redis.from_url(redis_url)
    assert 0 < redis.ttl(f"verdict:{key}") <= 24 * 3600
    assert 0 < redis.ttl("verdict:short") <= 30


def test_cache_hits_and_misses_are_counted_in_metrics(client: TestClient) -> None:
    client.get("/internal/cache/verdict/k1", headers=H)
    client.put("/internal/cache/verdict/k1", json={"value": {}}, headers=H)
    client.get("/internal/cache/verdict/k1", headers=H)
    text = client.get("/metrics").text
    assert 'cache_hits_total{cache="verdict"} 1.0' in text
    assert 'cache_misses_total{cache="verdict"} 1.0' in text


def test_cache_keys_are_restricted_and_values_are_objects(client: TestClient) -> None:
    assert client.get("/internal/cache/verdict/bad key!", headers=H).status_code == 422
    assert (
        client.put("/internal/cache/verdict/k", json={"value": "string"}, headers=H).status_code
        == 422
    )
    assert (
        client.put(
            "/internal/cache/verdict/k", json={"value": {}, "ttl_s": 0}, headers=H
        ).status_code
        == 422
    )


def test_all_storage_endpoints_need_the_internal_token(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    calls = [
        client.post("/internal/ideas", json={"user_id": uid, "title": "t", "text": "x"}),
        client.get(f"/internal/ideas?user_id={uid}"),
        client.post("/internal/verdicts", json={}),
        client.put("/internal/runs/r", json={}),
        client.post("/internal/approvals", json={}),
        client.get("/internal/cache/verdict/k"),
        client.put("/internal/cache/verdict/k", json={"value": {}}),
    ]
    assert {c.status_code for c in calls} == {401}
