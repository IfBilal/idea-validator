"""JWT access tokens (HS256, 60 minutes by default). Pure functions; the secret is passed in."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from common.errors import Unauthorized

ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
ISSUER = "idea-validator-gateway"
AUDIENCE = "idea-validator"


def issue_token(secret: str, user_id: str, ttl_s: int, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": issued,
        "exp": issued + timedelta(seconds=ttl_s),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def verify_token(secret: str, token: str) -> str:
    """Return the user id. Anything wrong (signature, algorithm, expiry, missing claim) is a 401."""
    return verify_claims(secret, token)[0]


def verify_claims(secret: str, token: str) -> tuple[str, float]:
    """Return ``(user id, expiry as a Unix timestamp)``; any problem is a 401."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid or expired token") from exc
    subject = claims["sub"]
    try:  # the subject goes into an internal URL path: it must be a UUID, nothing else
        if not isinstance(subject, str):
            raise ValueError("subject is not a string")
        return str(uuid.UUID(subject)), float(claims["exp"])
    except (ValueError, TypeError) as exc:
        raise Unauthorized("invalid or expired token") from exc
