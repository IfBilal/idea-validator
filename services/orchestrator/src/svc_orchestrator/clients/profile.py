"""M2 Profile/Session/Cache client: profile, verdict cache and persisted run state."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from common.http import ServiceClient
from common.schemas import StudentProfile

from .base import BaseClient


@dataclass(frozen=True)
class IdeaVersion:
    idea_id: str
    version: int
    text: str
    card: dict[str, Any]
    change_note: str | None = None


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

    async def get_idea_version(self, user_id: str, idea_id: str, version: int) -> IdeaVersion:
        """M2 contract: ``GET /internal/ideas/{id}/versions/{v}?user_id=`` -> text, card, note."""
        path = f"/internal/ideas/{idea_id}/versions/{version}?user_id={quote(user_id, safe='')}"
        data = self._object(await self._call("GET", path), "idea version")
        text = data.get("text")
        card = data.get("card", data.get("verdict_payload"))  # M2's stored name for the card
        if not isinstance(text, str) or not text:
            raise self.bad("M2 profile returned an idea version without text")
        note = data.get("change_note")
        return IdeaVersion(
            idea_id,
            version,
            text,
            card if isinstance(card, dict) else {},
            note if isinstance(note, str) else None,
        )

    async def put_idea_version(
        self,
        *,
        user_id: str,
        idea_id: str,
        version: int,
        text: str,
        change_note: str,
        card: dict[str, Any],
        idempotency_key: str,
        base_version: int,
    ) -> bool:
        """M2 contract (PR #48): idempotent ``PUT .../versions/{v}``; ``created`` False = replay.

        M2 answers 409 when ``base_version`` is not the latest, or the version exists under a
        different key. That surfaces as ``Conflict`` and is never retried.
        """
        path = f"/internal/ideas/{idea_id}/versions/{version}"
        body = {
            "user_id": user_id,
            "text": text,
            "change_note": change_note,
            "card": card,
            "idempotency_key": idempotency_key,
            "base_version": base_version,
        }
        data = self._object(await self._call("PUT", path, body), "saved version")
        created = data.get("created")
        if (
            not isinstance(created, bool)
            or data.get("idea_id") != idea_id
            or data.get("version") != version
        ):
            raise self.bad("M2 profile returned an invalid reply to a version save")
        return created
