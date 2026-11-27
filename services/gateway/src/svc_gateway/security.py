"""JWT access tokens (HS256, 60 minutes by default). Pure functions; the secret is passed in."""

from datetime import UTC, datetime, timedelta

import jwt

from common.errors import Unauthorized

ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32


def issue_token(secret: str, user_id: str, ttl_s: int, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {"sub": user_id, "iat": issued, "exp": issued + timedelta(seconds=ttl_s)}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def verify_token(secret: str, token: str) -> str:
    """Return the user id. Anything wrong (signature, algorithm, expiry, missing claim) is a 401."""
    try:
        claims = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "iat", "sub"]}
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid or expired token") from exc
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject:
        raise Unauthorized("invalid or expired token")
    return subject
