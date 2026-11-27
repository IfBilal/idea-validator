from datetime import UTC, datetime, timedelta

import jwt
import pytest

from common.errors import RateLimited, Unauthorized, ValidationFailed
from svc_gateway.api.routes import client_ip
from svc_gateway.ratelimit import MemoryRateLimiter, fingerprint
from svc_gateway.security import issue_token, verify_token
from svc_gateway.services.auth import check_password_policy

SECRET = "s" * 40
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# ---- JWT
def test_a_fresh_token_verifies_and_carries_the_user_id() -> None:
    token = issue_token(SECRET, "user-1", 3600)
    assert verify_token(SECRET, token) == "user-1"


def test_the_token_lives_exactly_the_configured_time() -> None:
    token = issue_token(SECRET, "u", 3600, NOW)
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["exp"] - claims["iat"] == 3600


@pytest.mark.parametrize(("offset_s", "valid"), [(-2, False), (-1, False), (2, True), (60, True)])
def test_expiry_boundary(offset_s: int, valid: bool) -> None:
    expires = datetime.now(UTC) + timedelta(seconds=offset_s)
    token = jwt.encode(
        {"sub": "u", "iat": expires - timedelta(hours=1), "exp": expires}, SECRET, "HS256"
    )
    if valid:
        assert verify_token(SECRET, token) == "u"
    else:
        with pytest.raises(Unauthorized):
            verify_token(SECRET, token)


def test_a_wrong_secret_a_tampered_token_and_garbage_are_all_401() -> None:
    token = issue_token(SECRET, "u", 3600)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    for bad in (token, tampered, "not.a.jwt", "", "a.b.c.d"):
        secret = "x" * 40 if bad == token else SECRET
        with pytest.raises(Unauthorized):
            verify_token(secret, bad)


def test_the_none_algorithm_is_refused() -> None:
    claims = {"sub": "u", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)}
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(Unauthorized):
        verify_token(SECRET, unsigned)


def test_another_signing_algorithm_is_refused() -> None:
    claims = {"sub": "u", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)}
    with pytest.raises(Unauthorized):
        verify_token(SECRET, jwt.encode(claims, SECRET, algorithm="HS512"))


@pytest.mark.parametrize("missing", ["sub", "exp", "iat"])
def test_a_token_without_a_required_claim_is_refused(missing: str) -> None:
    claims = {"sub": "u", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)}
    del claims[missing]
    with pytest.raises(Unauthorized):
        verify_token(SECRET, jwt.encode(claims, SECRET, algorithm="HS256"))


def test_an_empty_subject_is_refused() -> None:
    claims = {"sub": "", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)}
    with pytest.raises(Unauthorized):
        verify_token(SECRET, jwt.encode(claims, SECRET, algorithm="HS256"))


# ---- password policy (boundary values)
@pytest.mark.parametrize(
    ("length", "ok"), [(0, False), (9, False), (10, True), (11, True), (128, True)]
)
def test_password_length_boundaries(length: int, ok: bool) -> None:
    password = "x7Qz!" * 40
    password = password[:length]
    if ok:
        check_password_policy(password, 10)
    else:
        with pytest.raises(ValidationFailed):
            check_password_policy(password, 10)


@pytest.mark.parametrize("common", ["password123", "PASSWORD123", "1234567890", "Qwertyuiop"])
def test_common_passwords_are_refused_regardless_of_case(common: str) -> None:
    with pytest.raises(ValidationFailed, match="too common"):
        check_password_policy(common, 10)


# ---- rate limiter (equivalence classes and window boundaries)
async def test_exactly_the_limit_is_allowed_and_one_more_is_not() -> None:
    limiter = MemoryRateLimiter(lambda: 1000.0)
    for _ in range(5):
        await limiter.hit("login", "ip1", 5)
    with pytest.raises(RateLimited) as blocked:
        await limiter.hit("login", "ip1", 5)
    assert blocked.value.details["retry_after"] >= 1


async def test_limits_are_independent_per_identity_and_per_kind() -> None:
    limiter = MemoryRateLimiter(lambda: 1000.0)
    for _ in range(3):
        await limiter.hit("login", "ip1", 3)
    await limiter.hit("login", "ip2", 3)
    await limiter.hit("register", "ip1", 3)


async def test_the_counter_resets_in_the_next_window() -> None:
    clock = {"now": 119.0}
    limiter = MemoryRateLimiter(lambda: clock["now"])
    for _ in range(2):
        await limiter.hit("k", "i", 2)
    with pytest.raises(RateLimited) as blocked:
        await limiter.hit("k", "i", 2)
    assert blocked.value.details["retry_after"] == 1  # 1 s to the next minute boundary
    clock["now"] = 120.0
    await limiter.hit("k", "i", 2)


async def test_a_limit_of_zero_blocks_everything() -> None:
    with pytest.raises(RateLimited):
        await MemoryRateLimiter().hit("k", "i", 0)


def test_email_fingerprints_ignore_case_and_whitespace_and_hide_the_address() -> None:
    assert fingerprint(" Ada@Example.com ") == fingerprint("ada@example.com")
    assert "ada" not in fingerprint("ada@example.com")


# ---- client ip
class _Req:
    def __init__(self, headers: dict[str, str], host: str | None) -> None:
        self.headers = headers
        self.client = type("C", (), {"host": host})() if host else None


def test_forwarded_for_is_only_trusted_when_configured() -> None:
    req = _Req({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "1.2.3.4")
    assert client_ip(req, False) == "1.2.3.4"  # type: ignore[arg-type]
    assert client_ip(req, True) == "9.9.9.9"  # type: ignore[arg-type]
    assert client_ip(_Req({}, None), True) == "unknown"  # type: ignore[arg-type]
