"""HTTP client for M6: ``POST /ingest`` and ``GET /cards`` with the internal token.

Uses the shared ``ServiceClient``: a timeout on every call, at most ``max_retries`` retries (never
more than 2) on timeouts, transport errors and 429/502/503/504, none on other 4xx. The job calls
only M6; it has no other egress (the NetworkPolicy allows kb and nothing else).
"""

from typing import Any

import httpx

from common.http import ServiceClient
from ingestion.models import IngestOutcome

MAX_RETRIES = 2


class HttpKbClient:
    def __init__(
        self,
        base_url: str,
        *,
        internal_token: str | None,
        timeout_s: float = 30.0,
        max_retries: int = MAX_RETRIES,
        backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=min(max(max_retries, 0), MAX_RETRIES),
            internal_token=internal_token,
            backoff_s=backoff_s,
            transport=transport,
        )

    async def ingest(self, cards: list[dict[str, Any]]) -> list[IngestOutcome]:
        body = await self._http.post_json("/ingest", {"cards": cards})
        return [IngestOutcome.model_validate(r) for r in body["results"]]

    async def list_cards(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        page = await self._http.get_json("/cards", params={"limit": limit, "offset": offset})
        return list(page["items"]), int(page["total"])

    async def aclose(self) -> None:
        await self._http.aclose()
