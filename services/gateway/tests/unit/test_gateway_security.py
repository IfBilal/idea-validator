import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from common.errors import RateLimited, Unauthorized, ValidationFailed
from svc_gateway.api.routes import client_ip
from svc_gateway.ratelimit import MemoryRateLimiter, fingerprint
from svc_gateway.security import AUDIENCE, ISSUER, issue_token, verify_token
from svc_gateway.services.auth import check_password_policy

SECRET = "s" * 40
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
USER = str(uuid.uuid4())


def claims(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sub": USER,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


def encode(payload: dict[str, Any], secret: str = SECRET, alg: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=alg)


# ---- JWT
def test_a_fresh_token_verifies_and_carries_the_user_id() -> None:
    assert verify_token(SECRET, issue_token(SECRET, USER, 3600)) == USER


def test_the_token_lives_exactly_the_configured_time_and_names_issuer_and_audience() -> None:
    token = issue_token(SECRET, USER, 3600, NOW)
    decoded = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
        options={"verify_exp": False},
    )
    assert decoded["exp"] - decoded["iat"] == 3600
    assert (decoded["iss"], decoded["aud"]) == (ISSUER, AUDIENCE)


@pytest.mark.parametrize(("offset_s", "valid"), [(-2, False), (-1, False), (2, True), (60, True)])
def test_expiry_boundary(offset_s: int, valid: bool) -> None:
    expires = datetime.now(UTC) + timedelta(seconds=offset_s)
    token = encode(claims(iat=expires - timedelta(hours=1), exp=expires))
    if valid:
        assert verify_token(SECRET, token) == USER
    else:
        with pytest.raises(Unauthorized):
            verify_token(SECRET, token)


def test_a_wrong_secret_a_tampered_token_and_garbage_are_all_401() -> None:
    token = issue_token(SECRET, USER, 3600)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    for bad in (token, tampered, "not.a.jwt", "", "a.b.c.d"):
        secret = "x" * 40 if bad == token else SECRET
        with pytest.raises(Unauthorized):
            verify_token(secret, bad)


def test_the_none_algorithm_is_refused() -> None:
    unsigned = jwt.encode(claims(), key=None, algorithm="none")
    with pytest.raises(Unauthorized):
        verify_token(SECRET, unsigned)


def test_another_signing_algorithm_is_refused() -> None:
    with pytest.raises(Unauthorized):
        verify_token(SECRET, encode(claims(), alg="HS512"))


@pytest.mark.parametrize("missing", ["sub", "exp", "iat", "iss", "aud"])
def test_a_token_without_a_required_claim_is_refused(missing: str) -> None:
    with pytest.raises(Unauthorized):
        verify_token(SECRET, encode(claims(**{missing: None})))


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "someone-else"},
        {"aud": "another-service"},
        {"sub": ""},
        {"sub": "../../internal/admin"},
        {"sub": "not-a-uuid"},
        {"sub": 12345},
        {"sub": [USER]},
    ],
)
def test_wrong_issuer_audience_or_subject_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(Unauthorized):
        verify_token(SECRET, encode(claims(**changes)))


def test_a_token_from_the_future_is_refused() -> None:
    future = datetime.now(UTC) + timedelta(hours=2)
    with pytest.raises(Unauthorized):
        verify_token(SECRET, encode(claims(iat=future, exp=future + timedelta(hours=1))))


def test_the_subject_is_normalised_to_a_canonical_uuid() -> None:
    upper = USER.upper()
    assert verify_token(SECRET, encode(claims(sub=upper))) == USER


# ---- password policy (boundary values)
@pytest.mark.parametrize(
    ("length", "ok"), [(0, False), (9, False), (10, True), (11, True), (128, True)]
)
def test_password_length_boundaries(length: int, ok: bool) -> None:
    password = ("x7Qz!" * 40)[:length]
    if ok:
        check_password_policy(password, 10)
    else:
        with pytest.raises(ValidationFailed):
            check_password_policy(password, 10)


@pytest.mark.parametrize(
    "common", ["password123", "PASSWORD123", "1234567890", "Qwertyuiop", "aaaaaaaaaa", "ababababab"]
)
def test_common_or_trivial_passwords_are_refused_regardless_of_case(common: str) -> None:
    with pytest.raises(ValidationFailed, match="too common"):
        check_password_policy(common, 10)


def test_whitespace_only_passwords_and_passwords_containing_the_email_name_are_refused() -> None:
    with pytest.raises(ValidationFailed, match="whitespace"):
        check_password_policy(" " * 12, 10)
    with pytest.raises(ValidationFailed, match="email name"):
        check_password_policy("ada.lovelace-2026!", 10, "ada.lovelace@example.com")
    check_password_policy(
        "tab\there\nand-more9", 10, "ada@example.com"
    )  # any other character is fine


# ---- rate limiter (equivalence classes and window boundaries)
async def test_exactly_the_limit_is_allowed_and_one_more_is_not() -> None:
    limiter = MemoryRateLimiter(lambda: 1000.0)
    for _ in range(5):
        await limiter.hit("login", "ip1", 5)
    with pytest.raises(RateLimited) as blocked:
        await limiter.hit("login", "ip1", 5)
    assert blocked.value.details["retry_after"] >= 1


async def test_peek_blocks_only_once_the_limit_is_reached_and_never_counts() -> None:
    limiter = MemoryRateLimiter(lambda: 1000.0)
    for _ in range(10):
        await limiter.peek("fail", "e", 2)  # nothing counted, nothing blocked
    await limiter.hit("fail", "e", 2)
    await limiter.peek("fail", "e", 2)
    await limiter.hit("fail", "e", 2)
    with pytest.raises(RateLimited):
        await limiter.peek("fail", "e", 2)


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
    assert blocked.value.details["retry_after"] == 1
    clock["now"] = 120.0
    await limiter.hit("k", "i", 2)


async def test_a_limit_of_zero_blocks_everything() -> None:
    with pytest.raises(RateLimited):
        await MemoryRateLimiter().hit("k", "i", 0)


def test_email_fingerprints_ignore_case_and_whitespace_and_hide_the_address() -> None:
    assert fingerprint(" Ada@Example.com ") == fingerprint("ada@example.com")
    assert "ada" not in fingerprint("ada@example.com")


# ---- client ip: trusted proxies and spoofing
class _Req:
    def __init__(self, forwarded: str | None, host: str | None) -> None:
        self.headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
        self.client = type("C", (), {"host": host})() if host else None


def ip(forwarded: str | None, hops: int, host: str | None = "10.1.1.1") -> str:
    return client_ip(_Req(forwarded, host), hops)  # type: ignore[arg-type]


def test_without_a_trusted_proxy_the_header_is_ignored() -> None:
    assert ip("9.9.9.9", 0) == "10.1.1.1"


def test_behind_one_proxy_the_last_entry_is_the_client_and_forged_prefixes_are_ignored() -> None:
    assert ip("203.0.113.7", 1) == "203.0.113.7"
    assert ip("6.6.6.6, 7.7.7.7, 203.0.113.7", 1) == "203.0.113.7"  # attacker-added prefix


def test_behind_two_proxies_the_second_from_the_right_is_the_client() -> None:
    assert ip("6.6.6.6, 203.0.113.7, 10.9.9.9", 2) == "203.0.113.7"


@pytest.mark.parametrize(
    "forwarded", [None, "", " , ", "not-an-ip", "999.1.1.1", "1.2.3.4:80", "a" * 500]
)
def test_missing_or_invalid_values_fall_back_to_the_connection_peer(forwarded: str | None) -> None:
    assert ip(forwarded, 1) == "10.1.1.1"


def test_fewer_entries_than_trusted_hops_falls_back_to_the_peer() -> None:
    assert ip("203.0.113.7", 2) == "10.1.1.1"


def test_ipv6_addresses_are_normalised() -> None:
    assert ip("2001:DB8::1", 1) == "2001:db8::1"


def test_no_peer_and_no_header_is_unknown() -> None:
    assert ip(None, 0, host=None) == "unknown"
