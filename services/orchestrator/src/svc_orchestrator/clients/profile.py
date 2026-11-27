"""M2 Profile/Session/Cache client: profile, verdict cache and persisted run state."""

from typing import Any

from common.http import ServiceClient
from common.schemas import StudentProfile

from .base import BaseClient


class ProfileClient(BaseClient):
    name = "M2 profile"

    def __init__(self, http: ServiceClient, cache_http: ServiceClient) -> None:
        super().__init__(http)
        self._cache_http = cache_http  # separate client: the cache lookup has a 1 s timeout

    async def get_profile(self, user_id: str) -> StudentProfile:
        return self._parse(StudentProfile, await self._call("GET", f"/internal/profiles/{user_id}"))

    async def get_cached_verdict(self, key: str) -> dict[str, Any] | None:
        path = f"/internal/cache/verdict/{key}"
        data = self._object(await self._call("GET", path, http=self._cache_http), "cache response")
        value = data.get("value")
        return value if data.get("hit") and isinstance(value, dict) else None

    async def put_cached_verdict(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        path = f"/internal/cache/verdict/{key}"
        await self._call("PUT", path, {"value": value, "ttl_s": ttl_s}, http=self._cache_http)

    async def put_run(self, run_id: str, record: dict[str, Any]) -> None:
        await self._call("PUT", f"/internal/runs/{run_id}", record)
