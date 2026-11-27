"""Typed client for M2. Upstream failures become public-safe errors (no internal URLs)."""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from common.errors import (
    Conflict,
    DomainError,
    NotFound,
    Unauthorized,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from common.http import ServiceClient

T = TypeVar("T")
INVALID_LOGIN = "invalid email or password"


def _public(exc: DomainError, what: str) -> DomainError:
    """Map an M2 failure to an error that is safe to show a browser."""
    if isinstance(exc, Conflict):
        return Conflict("email is already registered")
    if isinstance(exc, NotFound):
        return NotFound(f"{what} not found")
    if isinstance(exc, ValidationFailed):
        return ValidationFailed("request failed validation")
    if isinstance(exc, Unauthorized):
        return Unauthorized(INVALID_LOGIN)
    if isinstance(exc, UpstreamTimeout):
        return UpstreamTimeout("the service took too long to answer")
    return UpstreamError("the service is temporarily unavailable")


class ProfileClient:
    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    async def _call(self, what: str, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except DomainError as exc:
            raise _public(exc, what) from exc

    async def register(self, email: str, password: str) -> str:
        body = {"email": email, "password": password}
        data = await self._call("user", lambda: self._client.post_json("/internal/users", body))
        return str(data["user_id"])

    async def login(self, email: str, password: str) -> str:
        body = {"email": email, "password": password}
        data = await self._call(
            "user", lambda: self._client.post_json("/internal/auth/login", body)
        )
        return str(data["user_id"])

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        data = await self._call(
            "profile", lambda: self._client.get_json(f"/internal/profiles/{user_id}")
        )
        return dict(data)

    async def put_profile(self, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        data = await self._call(
            "profile",
            lambda: self._client.request("PUT", f"/internal/profiles/{user_id}", json=body),
        )
        return dict(data.json())

    async def ready(self) -> bool:
        try:
            await self._client.get_json("/ready")
        except DomainError:
            return False
        return True
