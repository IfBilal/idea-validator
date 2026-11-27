"""M6 Knowledge Base client: hybrid search returning validated evidence items."""

from dataclasses import dataclass

from pydantic import ValidationError

from common.schemas import EvidenceItem

from .base import BaseClient


@dataclass(frozen=True)
class SearchResult:
    items: list[EvidenceItem]
    skipped: int  # malformed items dropped (never trusted)
    index_version: int | None


class KbClient(BaseClient):
    name = "M6 kb"

    async def search(self, query: str, *, run_id: str, k: int = 10) -> SearchResult:
        data = self._object(
            await self._call(
                "POST",
                "/search",
                {"query": query, "k": k, "mode": "hybrid", "rerank": True, "run_id": run_id},
            ),
            "search response",
        )
        raw = data.get("items")
        if not isinstance(raw, list):
            raise self.bad("M6 kb returned a search response without an items list")
        items: list[EvidenceItem] = []
        skipped = 0
        for entry in raw:
            try:
                items.append(EvidenceItem.model_validate(entry))
            except ValidationError:
                skipped += 1
        version = data.get("index_version")
        return SearchResult(items, skipped, version if isinstance(version, int) else None)
