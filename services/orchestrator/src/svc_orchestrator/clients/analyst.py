"""M7 Niche and Trend Analyst client: computed features from the evidence."""

from typing import Any

from .base import BaseClient


class AnalystClient(BaseClient):
    name = "M7 analyst"

    async def features(
        self, idea_text: str, evidence_ids: list[str], profile: dict[str, Any]
    ) -> dict[str, Any]:
        body = {"idea_text": idea_text, "evidence_ids": evidence_ids, "profile": profile}
        return self._object(await self._call("POST", "/features", body), "features response")
