"""M4 Scouts client: on-demand deep search (the scout fetches through M5, never directly)."""

from typing import Any

from .base import BaseClient


class ScoutsClient(BaseClient):
    name = "M4 scouts"

    async def scout(
        self, source: str, *, max_pages: int, query: str | None = None
    ) -> dict[str, Any]:
        body = {"source": source, "query": query, "max_pages": max_pages}
        return self._object(await self._call("POST", "/scout", body), "scout response")
