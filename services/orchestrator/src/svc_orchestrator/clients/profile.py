"""M2 Profile/Session/Cache client: profile, verdict cache and persisted run state."""

from dataclasses import dataclass
from typing import Any

from common.http import ServiceClient
from common.schemas import StudentProfile

from .base import BaseClient


@dataclass(frozen=True)
class IdeaVersion:
    idea_id: str
    version: int
    text: str
    card: dict[str, Any]


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

    async def get_idea_version(self, idea_id: str, version: int) -> IdeaVersion:
        """Assumed M2 contract: reply ``{idea_id, version, text, card}`` (docs/harness/05)."""
        path = f"/internal/ideas/{idea_id}/versions/{version}"
        data = self._object(await self._call("GET", path), "idea version")
        text, card = data.get("text"), data.get("card")
        if not isinstance(text, str) or not text:
            raise self.bad("M2 profile returned an idea version without text")
        return IdeaVersion(idea_id, version, text, card if isinstance(card, dict) else {})

    async def create_idea_version(self, idea_id: str, text: str, change_note: str) -> int:
        """Assumed M2 contract: body ``{text, change_note}``, reply ``{version}``."""
        body = {"text": text, "change_note": change_note}
        data = self._object(
            await self._call("POST", f"/internal/ideas/{idea_id}/versions", body), "new version"
        )
        number = data.get("version")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise self.bad("M2 profile returned a new version without a valid number")
        return number
