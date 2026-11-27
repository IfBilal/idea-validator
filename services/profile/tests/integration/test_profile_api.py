import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from svc_profile.config import Settings
from svc_profile.main import build_app
from svc_profile.models.profiles import ProfileUpdate
from svc_profile.services.profiles import ProfileService

pytestmark = pytest.mark.integration
HEADERS = {"X-Internal-Token": "tok"}
PROFILE: dict[str, Any] = {
    "skills": ["python", "react"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "portfolio",
    "interests": ["education"],
}


@pytest.fixture
def client(migrated_url: str, redis_url: str, engine) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    settings = Settings(database_url=migrated_url, redis_url=redis_url, internal_token="tok")
    with TestClient(build_app(settings)) as test_client:
        yield test_client


def register(client: TestClient, email: str = "ada@example.com") -> str:
    response = client.post(
        "/internal/users", json={"email": email, "password": "correct horse"}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    return str(response.json()["user_id"])


def test_register_and_login_over_http(client: TestClient) -> None:
    user_id = register(client)
    login = client.post(
        "/internal/auth/login",
        json={"email": "ada@example.com", "password": "correct horse"},
        headers=HEADERS,
    )
    assert login.status_code == 200
    assert login.json()["user_id"] == user_id


def test_login_failures_are_401_with_the_same_message(client: TestClient) -> None:
    register(client)
    bad = client.post(
        "/internal/auth/login",
        json={"email": "ada@example.com", "password": "wrong password"},
        headers=HEADERS,
    )
    unknown = client.post(
        "/internal/auth/login",
        json={"email": "who@example.com", "password": "wrong password"},
        headers=HEADERS,
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_duplicate_email_is_409_and_a_short_password_is_422(client: TestClient) -> None:
    register(client)
    duplicate = client.post(
        "/internal/users",
        json={"email": "ADA@example.com", "password": "correct horse"},
        headers=HEADERS,
    )
    short = client.post(
        "/internal/users", json={"email": "b@example.com", "password": "short"}, headers=HEADERS
    )
    assert duplicate.status_code == 409
    assert short.status_code == 422


def test_every_put_creates_a_new_version_and_old_versions_stay_readable(client: TestClient) -> None:
    user_id = register(client)
    first = client.put(f"/internal/profiles/{user_id}", json=PROFILE, headers=HEADERS)
    second = client.put(
        f"/internal/profiles/{user_id}", json={**PROFILE, "hours_per_week": 20}, headers=HEADERS
    )
    assert (first.json()["version"], second.json()["version"]) == (1, 2)
    current = client.get(f"/internal/profiles/{user_id}", headers=HEADERS).json()
    old = client.get(f"/internal/profiles/{user_id}?version=1", headers=HEADERS).json()
    assert current["hours_per_week"] == 20
    assert old["hours_per_week"] == 10
    assert current["user_id"] == user_id


def test_profile_not_found_cases(client: TestClient) -> None:
    user_id = register(client)
    assert client.get(f"/internal/profiles/{user_id}", headers=HEADERS).status_code == 404
    ghost = uuid.uuid4()
    assert (
        client.put(f"/internal/profiles/{ghost}", json=PROFILE, headers=HEADERS).status_code == 404
    )
    client.put(f"/internal/profiles/{user_id}", json=PROFILE, headers=HEADERS)
    assert client.get(f"/internal/profiles/{user_id}?version=9", headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        {"hours_per_week": 81},
        {"weeks_available": 0},
        {"team_size": 11},
        {"goal": "x"},
        {"extra": 1},
    ],
)
def test_invalid_profiles_are_422(client: TestClient, bad: dict[str, Any]) -> None:
    user_id = register(client)
    response = client.put(f"/internal/profiles/{user_id}", json={**PROFILE, **bad}, headers=HEADERS)
    assert response.status_code == 422


def test_skills_are_limited_to_30_items_of_50_characters(client: TestClient) -> None:
    user_id = register(client)
    too_many = {**PROFILE, "skills": ["x"] * 31}
    too_long = {**PROFILE, "skills": ["x" * 51]}
    for body in (too_many, too_long):
        assert (
            client.put(f"/internal/profiles/{user_id}", json=body, headers=HEADERS).status_code
            == 422
        )


def test_internal_endpoints_need_the_token(client: TestClient) -> None:
    assert client.get(f"/internal/profiles/{uuid.uuid4()}").status_code == 401
    assert (
        client.post("/internal/users", json={"email": "a@b.co", "password": "x" * 12}).status_code
        == 401
    )


def test_ready_reports_the_database(client: TestClient) -> None:
    assert client.get("/ready").json() == {
        "status": "ready",
        "checks": {"postgres": True, "redis": True},
    }


async def test_concurrent_updates_get_distinct_consecutive_versions(sessions, engine) -> None:  # type: ignore[no-untyped-def]
    from svc_profile.services.auth import AuthService

    user_id = await AuthService(sessions, Settings()).register("race@example.com", "correct horse")
    profiles = ProfileService(sessions)
    update = ProfileUpdate.model_validate(PROFILE)
    results = await asyncio.gather(*(profiles.put(user_id, update) for _ in range(6)))
    assert sorted(r.version or 0 for r in results) == [1, 2, 3, 4, 5, 6]


def test_the_service_refuses_to_start_without_a_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.config import ConfigError

    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigError, match="DATABASE_URL"), TestClient(build_app(Settings())):
        pass
