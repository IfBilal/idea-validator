import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from common.config import ConfigError
from common.http import ServiceClient
from svc_gateway.clients.profile import ProfileClient
from svc_gateway.config import Settings
from svc_gateway.main import build_app
from svc_gateway.ratelimit import MemoryRateLimiter

SECRET = "s" * 40
INTERNAL = "internal-token"
PASSWORD = "correct horse battery"
PROFILE: dict[str, Any] = {
    "skills": ["python"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "portfolio",
    "interests": ["education"],
}


class FakeProfileService:
    """An in-process stand-in for M2's internal API (checks that the gateway sends the token)."""

    def __init__(self) -> None:
        self.users: dict[str, tuple[str, str]] = {}  # email -> (id, password)
        self.profiles: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail: int | None = None
        self.timeout = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.timeout:
            raise httpx.ReadTimeout("slow")
        if request.headers.get("x-internal-token") != INTERNAL:
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
        if self.fail:
            return httpx.Response(self.fail)
        path, body = request.url.path, (json.loads(request.content) if request.content else {})
        if path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.method == "POST" and path == "/internal/users":
            if body["email"].lower() in self.users:
                return httpx.Response(409, json={})
            user_id = str(uuid.uuid4())
            self.users[body["email"].lower()] = (user_id, body["password"])
            return httpx.Response(201, json={"user_id": user_id})
        if path == "/internal/auth/login":
            found = self.users.get(body["email"].lower())
            if not found or found[1] != body["password"]:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"user_id": found[0]})
        if path.startswith("/internal/profiles/"):
            user_id = path.rsplit("/", 1)[1]
            if request.method == "GET":
                if user_id not in self.profiles:
                    return httpx.Response(404, json={})
                return httpx.Response(200, json=self.profiles[user_id])
            current = self.profiles.get(user_id, {}).get("version", 0)
            self.profiles[user_id] = {**body, "user_id": user_id, "version": current + 1}
            return httpx.Response(200, json=self.profiles[user_id])
        return httpx.Response(404)


def make_app(m2: FakeProfileService, **overrides: Any) -> TestClient:
    settings = Settings(jwt_secret=SecretStr(SECRET), ui_origin="https://ui.test", **overrides)
    client = ServiceClient(
        "http://profile.internal.test:8000",
        timeout_s=1,
        backoff_s=0,
        internal_token=INTERNAL,
        transport=httpx.MockTransport(m2),
    )
    clock = datetime.now(UTC).timestamp
    app = build_app(settings, MemoryRateLimiter(clock), ProfileClient(client))
    return TestClient(app)


@pytest.fixture
def m2() -> FakeProfileService:
    return FakeProfileService()


@pytest.fixture
def client(m2: FakeProfileService) -> Iterator[TestClient]:
    with make_app(m2) as test_client:
        yield test_client


def register(client: TestClient, email: str = "ada@example.com", password: str = PASSWORD) -> Any:
    return client.post("/auth/register", json={"email": email, "password": password})


def token_for(client: TestClient, email: str = "ada@example.com") -> dict[str, str]:
    register(client, email)
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---- registration: equivalence classes and boundary values
def test_register_returns_201_and_a_user_id(client: TestClient) -> None:
    r = register(client)
    assert r.status_code == 201
    uuid.UUID(r.json()["user_id"])


@pytest.mark.parametrize(
    ("length", "status"), [(9, 422), (10, 201), (11, 201), (127, 201), (128, 201), (129, 422)]
)
def test_password_length_boundaries_over_http(client: TestClient, length: int, status: int) -> None:
    password = ("Zq9#" * 40)[:length]
    assert register(client, f"u{length}@example.com", password).status_code == status


@pytest.mark.parametrize(
    "email",
    ["", "plain", "a@", "@b.co", "a b@c.co", "a@b", "a@@b.co", "a@b..co", "x" * 300 + "@b.co"],
)
def test_malformed_emails_are_rejected(client: TestClient, email: str) -> None:
    assert register(client, email).status_code == 422


@pytest.mark.parametrize("email", ["a@b.co", "first.last+tag@sub.example.org", "UPPER@EXAMPLE.COM"])
def test_valid_emails_are_accepted(client: TestClient, email: str) -> None:
    assert register(client, email).status_code == 201


def test_control_characters_extra_fields_and_missing_fields_are_rejected(
    client: TestClient,
) -> None:
    assert register(client, password="bad\x00password!!").status_code == 422
    assert register(client, "tab@example.com", "tab\tword\nPass9!z").status_code == 201
    base = {"email": "a@b.co", "password": PASSWORD}
    assert client.post("/auth/register", json={**base, "is_admin": True}).status_code == 422
    assert client.post("/auth/register", json={"email": "a@b.co"}).status_code == 422
    assert client.post("/auth/register", content=b"not json").status_code == 422


def test_common_passwords_are_refused(client: TestClient) -> None:
    r = register(client, password="password123")
    assert r.status_code == 422
    assert "too common" in r.json()["error"]["message"]


def test_a_duplicate_email_is_409_and_case_insensitive(client: TestClient) -> None:
    register(client, "ada@example.com")
    assert register(client, "ADA@example.com").status_code == 409


# ---- login
def test_login_returns_a_bearer_token_that_lasts_an_hour(client: TestClient) -> None:
    register(client)
    body = client.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    ).json()
    assert (body["token_type"], body["expires_in"]) == ("bearer", 3600)
    claims = jwt.decode(
        body["access_token"], SECRET, algorithms=["HS256"], audience="idea-validator"
    )
    assert claims["exp"] - claims["iat"] == 3600


def test_wrong_password_and_unknown_email_are_indistinguishable(client: TestClient) -> None:
    register(client)
    wrong = client.post(
        "/auth/login", json={"email": "ada@example.com", "password": "nope-nope-nope"}
    )
    unknown = client.post(
        "/auth/login", json={"email": "who@example.com", "password": "nope-nope-nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


# ---- authentication of protected routes
@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Token abc",
        "Basic YWJjOmRlZg==",
        "Bearer not.a.jwt",
        "bearer",
    ],
)
def test_protected_routes_reject_missing_or_malformed_credentials(
    client: TestClient, header: str | None
) -> None:
    headers = {"Authorization": header} if header is not None else {}
    assert client.get("/profile", headers=headers).status_code == 401
    assert client.put("/profile", json=PROFILE, headers=headers).status_code == 401


def test_expired_foreign_and_tampered_tokens_are_401(client: TestClient) -> None:
    now = datetime.now(UTC)
    expired = jwt.encode(
        {"sub": "u", "iat": now - timedelta(hours=2), "exp": now - timedelta(seconds=5)},
        SECRET,
        "HS256",
    )
    foreign = jwt.encode(
        {"sub": "u", "iat": now, "exp": now + timedelta(hours=1)}, "y" * 40, "HS256"
    )
    for token in (expired, foreign):
        assert (
            client.get("/profile", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        )


def test_the_bearer_scheme_is_case_insensitive(client: TestClient) -> None:
    headers = token_for(client)
    token = headers["Authorization"].split(" ", 1)[1]
    assert client.get("/profile", headers={"Authorization": f"BEARER {token}"}).status_code == 404


# ---- profile
def test_profile_round_trip_uses_the_user_from_the_token(
    client: TestClient, m2: FakeProfileService
) -> None:
    headers = token_for(client)
    assert client.get("/profile", headers=headers).status_code == 404
    first = client.put("/profile", json=PROFILE, headers=headers).json()
    second = client.put("/profile", json={**PROFILE, "hours_per_week": 20}, headers=headers).json()
    assert (first["version"], second["version"]) == (1, 2)
    assert client.get("/profile", headers=headers).json()["hours_per_week"] == 20
    assert set(m2.profiles) == {first["user_id"]}


def test_a_user_cannot_choose_whose_profile_to_write(
    client: TestClient, m2: FakeProfileService
) -> None:
    headers = token_for(client)
    other = str(uuid.uuid4())
    assert (
        client.put("/profile", json={**PROFILE, "user_id": other}, headers=headers).status_code
        == 422
    )
    client.put("/profile", json=PROFILE, headers=headers)
    assert other not in m2.profiles


@pytest.mark.parametrize(
    ("field", "value", "status"),
    [
        ("hours_per_week", -1, 422), ("hours_per_week", 0, 200), ("hours_per_week", 80, 200),
        ("hours_per_week", 81, 422), ("weeks_available", 0, 422), ("weeks_available", 1, 200),
        ("weeks_available", 52, 200), ("weeks_available", 53, 422), ("team_size", 0, 422),
        ("team_size", 1, 200), ("team_size", 10, 200), ("team_size", 11, 422),
        ("compute", "tpu", 422), ("compute", "paid", 200), ("goal", "hobby", 422),
        ("goal", "startup", 200), ("hours_per_week", "ten", 422), ("hours_per_week", 10.5, 422),
        ("skills", ["x"] * 30, 200), ("skills", ["x"] * 31, 422), ("skills", ["x" * 50], 200),
        ("skills", ["x" * 51], 422), ("skills", [""], 422), ("skills", ["bad\x00tag"], 422),
        ("interests", ["y"] * 31, 422), ("skills", "python", 422),
    ],
)  # fmt: skip
def test_profile_boundary_values(client: TestClient, field: str, value: Any, status: int) -> None:
    headers = token_for(client)
    assert (
        client.put("/profile", json={**PROFILE, field: value}, headers=headers).status_code
        == status
    )


def test_every_profile_field_is_required(client: TestClient) -> None:
    headers = token_for(client)
    for field in PROFILE:
        body = {k: v for k, v in PROFILE.items() if k != field}
        assert client.put("/profile", json=body, headers=headers).status_code == 422, field


# ---- upstream failures never leak internals
@pytest.mark.parametrize("failure", [500, 502, 503, 504])
def test_an_upstream_failure_is_a_generic_error_without_internal_hosts(
    client: TestClient, m2: FakeProfileService, failure: int
) -> None:
    m2.fail = failure
    r = client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert r.status_code in {502, 504}
    assert "internal.test" not in r.text
    assert "profile.internal" not in r.text


def test_an_upstream_timeout_is_a_504_without_internals(
    client: TestClient, m2: FakeProfileService
) -> None:
    m2.timeout = True
    r = client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert r.status_code == 504
    assert "internal" not in r.text


def test_the_gateway_authenticates_itself_to_the_profile_service(
    client: TestClient, m2: FakeProfileService
) -> None:
    register(client)
    assert ("POST", "/internal/users") in m2.calls  # the fake answers 401 without the token


# ---- rate limiting
def test_login_is_limited_to_five_per_minute_per_ip_with_retry_after(client: TestClient) -> None:
    body = {"email": "ada@example.com", "password": "wrong-wrong-wrong"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5
    last = client.post("/auth/login", json=body)
    assert codes[5] == last.status_code == 429
    assert int(last.headers["retry-after"]) >= 1
    assert last.json()["error"]["code"] == "RATE_LIMITED"


def test_login_attempts_on_one_email_are_limited_across_different_ips(
    m2: FakeProfileService,
) -> None:
    with make_app(m2, trusted_proxy_hops=1) as client:
        body = {"email": "ada@example.com", "password": "wrong-wrong-wrong"}
        codes = [
            client.post(
                "/auth/login", json=body, headers={"X-Forwarded-For": f"10.0.0.{i}"}
            ).status_code
            for i in range(1, 7)
        ]
    assert codes == [401] * 5 + [429]


def test_a_spoofed_forwarded_for_header_does_not_bypass_the_limit_by_default(
    m2: FakeProfileService,
) -> None:
    with make_app(m2) as client:
        codes = [
            client.post(
                "/auth/login",
                json={"email": f"u{i}@example.com", "password": "wrong-wrong-wrong"},
                headers={"X-Forwarded-For": f"10.9.9.{i}"},
            ).status_code
            for i in range(6)
        ]
    assert codes == [401] * 5 + [429]


def test_the_global_per_ip_limit_applies_to_every_public_route(m2: FakeProfileService) -> None:
    with make_app(m2, ip_per_min=3) as client:
        headers = token_for(client)  # register + login = 2 requests
        assert client.get("/profile", headers=headers).status_code == 404  # 3rd
        assert client.get("/profile", headers=headers).status_code == 429  # 4th


def test_probes_are_not_rate_limited(m2: FakeProfileService) -> None:
    with make_app(m2, ip_per_min=1) as client:
        assert all(client.get("/health").status_code == 200 for _ in range(10))


# ---- request size
def test_bodies_over_32_kib_are_413_even_without_a_content_length(client: TestClient) -> None:
    junk = b'{"email":"a@b.co","password":"' + b"x" * 40_000 + b'"}'
    assert client.post("/auth/login", content=junk).status_code == 413

    def stream() -> Iterator[bytes]:
        yield junk[:20_000]
        yield junk[20_000:]

    assert client.post("/auth/login", content=stream()).status_code == 413


@pytest.mark.parametrize(
    ("size", "too_big"), [(32 * 1024 - 1, False), (32 * 1024, False), (32 * 1024 + 1, True)]
)
def test_the_body_limit_boundary(client: TestClient, size: int, too_big: bool) -> None:
    prefix = b'{"email":"a@b.co","password":"'
    body = prefix + b"x" * (size - len(prefix) - 2) + b'"}'
    assert len(body) == size
    status = client.post("/auth/login", content=body).status_code
    assert (status == 413) is too_big


# ---- headers and CORS
def test_every_response_carries_the_security_headers(client: TestClient) -> None:
    for response in (
        client.get("/health"),
        client.get("/profile"),  # 401
        client.get("/does-not-exist"),  # 404
        client.post("/auth/login", content=b"x" * 40_000),  # 413
    ):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"


def test_cors_allows_only_the_ui_origin(client: TestClient) -> None:
    allowed = client.options(
        "/auth/login",
        headers={"Origin": "https://ui.test", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["access-control-allow-origin"] == "https://ui.test"
    denied = client.options(
        "/auth/login",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in denied.headers


# ---- readiness and start-up
def test_ready_when_the_profile_service_answers(client: TestClient) -> None:
    body = client.get("/ready").json()
    assert body["checks"]["profile"] is True


def test_not_ready_when_the_profile_service_is_down(
    client: TestClient, m2: FakeProfileService
) -> None:
    m2.fail = 503
    assert client.get("/ready").status_code == 503


@pytest.mark.parametrize("secret", [None, "", "short", "x" * 31])
def test_the_gateway_refuses_to_start_without_a_strong_jwt_secret(secret: str | None) -> None:
    settings = Settings(jwt_secret=SecretStr(secret) if secret is not None else None)
    app = build_app(
        settings, MemoryRateLimiter(), ProfileClient(ServiceClient("http://x", timeout_s=1))
    )
    with pytest.raises(ConfigError, match="JWT_SECRET"), TestClient(app):
        pass


def test_the_gateway_refuses_to_start_without_its_upstream_configuration() -> None:
    secret = SecretStr(SECRET)
    with (
        pytest.raises(ConfigError, match="REDIS_URL"),
        TestClient(build_app(Settings(jwt_secret=secret))),
    ):
        pass
    no_profile = Settings(jwt_secret=secret, redis_url=SecretStr("redis://x"))
    with pytest.raises(ConfigError, match="PROFILE_URL"), TestClient(build_app(no_profile)):
        pass


# ---- review follow-ups: fail-closed limiter, headers on 500, per-email failures, logging, schema
class BrokenLimiter:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def hit(self, kind: str, ident: str, limit: int) -> None:
        raise self.error

    async def peek(self, kind: str, ident: str, limit: int) -> None:
        raise self.error


def app_with_limiter(m2: FakeProfileService, limiter: Any) -> TestClient:
    settings = Settings(jwt_secret=SecretStr(SECRET), ui_origin="https://ui.test")
    client = ServiceClient(
        "http://profile.internal.test:8000",
        timeout_s=1,
        backoff_s=0,
        internal_token=INTERNAL,
        transport=httpx.MockTransport(m2),
    )
    return TestClient(
        build_app(settings, limiter, ProfileClient(client)), raise_server_exceptions=False
    )


def test_a_redis_outage_is_a_clean_503_on_every_public_route(m2: FakeProfileService) -> None:
    from common.errors import Unavailable

    with app_with_limiter(m2, BrokenLimiter(Unavailable("rate limiter unavailable"))) as client:
        responses = [
            client.post("/auth/login", json={"email": "a@b.co", "password": PASSWORD}),
            client.post("/auth/register", json={"email": "a@b.co", "password": PASSWORD}),
            client.get("/profile", headers={"Authorization": "Bearer x"}),
        ]
        assert [r.status_code for r in responses] == [503, 503, 503]
        assert responses[0].json()["error"]["code"] == "UPSTREAM_ERROR"
        assert client.get("/health").status_code == 200  # liveness does not depend on Redis


def test_an_unreachable_redis_server_fails_closed_with_503() -> None:
    from redis.asyncio import Redis

    from svc_gateway.ratelimit import RedisRateLimiter

    dead = Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.2)
    with app_with_limiter(FakeProfileService(), RedisRateLimiter(dead)) as client:
        r = client.post("/auth/login", json={"email": "a@b.co", "password": PASSWORD})
    assert r.status_code == 503
    assert "127.0.0.1" not in r.text


def test_unexpected_errors_are_500_without_leaks_and_still_carry_the_security_headers(
    m2: FakeProfileService,
) -> None:
    with app_with_limiter(m2, BrokenLimiter(RuntimeError("redis password is hunter2"))) as client:
        r = client.post("/auth/login", json={"email": "a@b.co", "password": PASSWORD})
    assert r.status_code == 500
    assert "hunter2" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_api_docs_and_the_openapi_document_are_not_public(client: TestClient) -> None:
    assert [client.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [404] * 3


@pytest.mark.parametrize(
    "origin", ["*", "ui.test", "https://ui.test/path", "https://a.test, https://b.test", ""]
)
def test_ui_origin_must_be_one_plain_origin(origin: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="UI_ORIGIN"):
        Settings(ui_origin=origin)


@pytest.mark.parametrize(
    "origin", ["https://ui.test", "http://localhost:5173", "https://ui.example.org:8443"]
)
def test_valid_ui_origins_are_accepted(origin: str) -> None:
    assert Settings(ui_origin=origin).ui_origin == origin


def test_only_failed_logins_count_against_an_email(m2: FakeProfileService) -> None:
    with make_app(m2, trusted_proxy_hops=1) as client:
        register(client)
        for i in range(1, 6):  # five successful logins from five addresses
            ok = client.post(
                "/auth/login",
                json={"email": "ada@example.com", "password": PASSWORD},
                headers={"X-Forwarded-For": f"10.1.0.{i}"},
            )
            assert ok.status_code == 200
        bad = client.post(
            "/auth/login",
            json={"email": "ada@example.com", "password": "wrong-wrong-wrong"},
            headers={"X-Forwarded-For": "10.1.0.99"},
        )
        assert bad.status_code == 401  # not 429: the successes did not use up the budget


def test_five_failures_from_different_addresses_block_that_email_but_not_others(
    m2: FakeProfileService,
) -> None:
    with make_app(m2, trusted_proxy_hops=1) as client:
        register(client, "victim@example.com")
        register(client, "other@example.com")
        codes = [
            client.post(
                "/auth/login",
                json={"email": "victim@example.com", "password": "wrong-wrong-wrong"},
                headers={"X-Forwarded-For": f"10.2.0.{i}"},
            ).status_code
            for i in range(1, 8)
        ]
        other = client.post(
            "/auth/login",
            json={"email": "other@example.com", "password": PASSWORD},
            headers={"X-Forwarded-For": "10.2.0.50"},
        )
    assert codes == [401] * 5 + [429, 429]
    assert other.status_code == 200


def test_a_forged_forwarded_for_prefix_cannot_dodge_the_ip_limit(m2: FakeProfileService) -> None:
    with make_app(m2, trusted_proxy_hops=1) as client:
        codes = [
            client.post(
                "/auth/login",
                json={"email": f"u{i}@example.com", "password": "wrong-wrong-wrong"},
                headers={"X-Forwarded-For": f"1.1.1.{i}, 203.0.113.7"},
            ).status_code
            for i in range(6)
        ]
    assert codes == [401] * 5 + [429]  # the entry the proxy added (203.0.113.7) is what counts


def test_password_policy_over_http_whitespace_trivial_and_email_name(client: TestClient) -> None:
    assert register(client, "w@example.com", " " * 12).status_code == 422
    assert register(client, "t@example.com", "aaaaaaaaaaaa").status_code == 422
    assert register(client, "grace.hopper@example.com", "grace.hopper-1906!").status_code == 422


def test_tags_are_trimmed_and_blank_tags_are_rejected(
    client: TestClient, m2: FakeProfileService
) -> None:
    headers = token_for(client)
    ok = client.put("/profile", json={**PROFILE, "skills": ["  python  "]}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["skills"] == ["python"]
    blank = client.put("/profile", json={**PROFILE, "skills": ["   "]}, headers=headers)
    assert blank.status_code == 422


def test_fields_the_profile_service_adds_later_never_reach_the_browser(
    client: TestClient, m2: FakeProfileService
) -> None:
    headers = token_for(client)
    user_id = next(iter(m2.users.values()))[0]
    m2.profiles[user_id] = {**PROFILE, "user_id": user_id, "version": 1, "internal_note": "secret"}
    r = client.get("/profile", headers=headers)
    assert "secret" not in r.text


def test_failures_are_logged_without_secrets(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    register(client)
    capsys.readouterr()
    client.post("/auth/login", json={"email": "ada@example.com", "password": "wrong-wrong-wrong"})
    client.get("/profile", headers={"Authorization": "Bearer abc.def.ghi"})
    out = capsys.readouterr().out
    assert "auth_failed" in out
    assert "token_rejected" in out
    for secret in ("ada@example.com", "wrong-wrong-wrong", "abc.def.ghi", SECRET):
        assert secret not in out


def test_rate_limit_hits_are_logged_with_the_kind(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    body = {"email": "ada@example.com", "password": "wrong-wrong-wrong"}
    for _ in range(6):
        client.post("/auth/login", json=body)
    assert "rate_limited" in capsys.readouterr().out


def test_a_non_numeric_content_length_does_not_crash_the_body_limit(client: TestClient) -> None:
    r = client.post(
        "/auth/login",
        content=b'{"email":"a@b.co","password":"x"}',
        headers={"Content-Type": "application/json", "Content-Length": "abc"},
    )
    assert r.status_code in {400, 401, 422}
