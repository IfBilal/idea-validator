import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from redis import Redis

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


def idea(client: TestClient, uid: str) -> str:
    r = client.post("/internal/ideas", json={"user_id": uid, "title": "T", "text": "v1"}, headers=H)
    assert r.status_code == 201
    return str(r.json()["idea_id"])


def put(client: TestClient, uid: str, iid: str, version: int = 2, **over: Any) -> Any:
    body: dict[str, Any] = {
        "user_id": uid,
        "text": "v2",
        "change_note": "tweak",
        "card": {"verdict": None, "x": [1, {"y": 2.5}]},
        "idempotency_key": "k-1",
        "base_version": version - 1,
    }
    body.update(over)
    return client.put(f"/internal/ideas/{iid}/versions/{version}", json=body, headers=H)


def test_create_returns_idea_id_and_version_one_has_null_card(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    r = client.get(f"/internal/ideas/{iid}/versions/1", params={"user_id": uid}, headers=H)
    body = r.json()
    assert r.status_code == 200
    assert (body["idea_id"], body["version"], body["text"]) == (iid, 1, "v1")
    assert body["card"] is None
    assert body["change_note"] is None


def test_put_creates_next_version_and_stores_card_verbatim(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    r = put(client, uid, iid)
    assert r.status_code == 200
    assert r.json() == {"idea_id": iid, "version": 2, "created": True}
    got = client.get(f"/internal/ideas/{iid}/versions/2", params={"user_id": uid}, headers=H).json()
    assert got["card"] == {"verdict": None, "x": [1, {"y": 2.5}]}
    assert (got["text"], got["change_note"]) == ("v2", "tweak")


def test_same_key_same_content_is_idempotent(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    put(client, uid, iid)
    again = put(client, uid, iid)
    assert again.status_code == 200
    assert again.json() == {"idea_id": iid, "version": 2, "created": False}
    versions = client.get(
        f"/internal/ideas/{iid}/versions", params={"user_id": uid}, headers=H
    ).json()
    assert len(versions) == 2


@pytest.mark.parametrize(
    "change", [{"text": "other"}, {"change_note": "x"}, {"card": {"verdict": None}}]
)
def test_same_key_different_content_conflicts(client: TestClient, change: dict[str, Any]) -> None:
    uid = user(client)
    iid = idea(client, uid)
    put(client, uid, iid)
    assert put(client, uid, iid, **change).status_code == 409


def test_stale_base_version_conflicts(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    assert put(client, uid, iid).status_code == 200
    r = put(client, uid, iid, idempotency_key="k-2")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_base_version_ahead_of_latest_conflicts(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    assert put(client, uid, iid, version=3).status_code == 409


def test_path_version_must_be_base_plus_one(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    assert put(client, uid, iid, version=2, base_version=2).status_code == 422


def test_chain_of_versions(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    for n in range(2, 6):
        assert put(client, uid, iid, version=n, idempotency_key=f"k{n}").json()["version"] == n


def test_foreign_and_unknown_ideas_are_identical_404s(client: TestClient) -> None:
    owner = user(client)
    other = user(client, "bob@example.com")
    iid = idea(client, owner)
    foreign = put(client, other, iid)
    unknown = put(client, other, str(uuid.uuid4()))
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"]
    got = client.get(f"/internal/ideas/{iid}/versions/1", params={"user_id": other}, headers=H)
    missing = client.get(f"/internal/ideas/{iid}/versions/9", params={"user_id": owner}, headers=H)
    assert got.status_code == missing.status_code == 404
    # nothing was written by the foreign attempt
    listed = client.get(f"/internal/ideas/{iid}/versions", params={"user_id": owner}, headers=H)
    assert len(listed.json()) == 1


def test_internal_token_is_required(client: TestClient) -> None:
    r = client.put(f"/internal/ideas/{uuid.uuid4()}/versions/2", json={})
    assert r.status_code in (401, 403)


@pytest.mark.parametrize(
    "over",
    [{"extra": 1}, {"idempotency_key": "bad key"}, {"text": ""}, {"card": {"a": "x" * 270_000}}],
)
def test_validation_errors_use_the_json_envelope(client: TestClient, over: dict[str, Any]) -> None:
    uid = user(client)
    iid = idea(client, uid)
    r = put(client, uid, iid, **over)
    assert r.status_code == 422
    assert "error" in r.json()


def test_path_version_zero_is_rejected(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    assert put(client, uid, iid, version=0, base_version=1).status_code == 422


def test_legacy_versions_have_null_card_and_a_put_chains_on_top(client: TestClient) -> None:
    uid = user(client)
    iid = idea(client, uid)
    client.post(
        f"/internal/ideas/{iid}/versions", json={"user_id": uid, "text": "legacy"}, headers=H
    )
    got = client.get(f"/internal/ideas/{iid}/versions/2", params={"user_id": uid}, headers=H)
    assert got.json()["card"] is None
    assert put(client, uid, iid, version=3).status_code == 200


def test_twenty_parallel_puts_exactly_one_wins(
    migrated_url: str,
    redis_url: str,
    engine: object,
) -> None:
    Redis.from_url(redis_url).flushdb()
    settings = Settings(database_url=migrated_url, redis_url=redis_url, internal_token="tok")

    async def run() -> list[int]:
        app = build_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.post(
                    "/internal/users",
                    json={"email": "p@example.com", "password": "correct horse"},
                    headers=H,
                )
                uid = r.json()["user_id"]
                r = await c.post(
                    "/internal/ideas", json={"user_id": uid, "title": "T", "text": "v1"}, headers=H
                )
                iid = r.json()["idea_id"]

                async def one(n: int) -> int:
                    body = {
                        "user_id": uid,
                        "text": f"v2-{n}",
                        "change_note": None,
                        "card": {"n": n},
                        "idempotency_key": f"key-{n}",
                        "base_version": 1,
                    }
                    resp = await c.put(f"/internal/ideas/{iid}/versions/2", json=body, headers=H)
                    return resp.status_code

                return list(await asyncio.gather(*(one(n) for n in range(20))))

    codes = asyncio.run(run())
    assert codes.count(200) == 1
    assert codes.count(409) == 19
