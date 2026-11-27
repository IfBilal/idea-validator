"""Typed HTTP client for M6 (assumed endpoints, see README): cards, batch fetch and search."""

from collections.abc import Awaitable, Sequence
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from common.errors import DomainError, Unavailable, UpstreamError
from common.http import ServiceClient

from ..models.cards import Card, EvidenceHit

_CARDS = TypeAdapter(list[Card])
_HITS = TypeAdapter(list[EvidenceHit])


class KnowledgeBaseClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float,
        max_retries: int = 2,
        internal_token: str | None = None,
        backoff_s: float = 0.2,
        page_size: int = 500,
        batch_size: int = 100,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=max_retries,
            internal_token=internal_token,
            backoff_s=backoff_s,
            transport=transport,
        )
        self._page_size = page_size
        self._batch_size = batch_size

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ping(self) -> bool:
        try:
            await self._http.request("GET", "/ready")
        except DomainError:
            return False
        return True

    @staticmethod
    async def _json(call: Awaitable[Any]) -> Any:
        try:
            return await call
        except ValueError as exc:  # body was not JSON
            raise UpstreamError("knowledge base returned a malformed response") from exc

    @staticmethod
    def _items(payload: Any) -> Any:
        try:
            return payload["items"]
        except (KeyError, TypeError) as exc:
            raise UpstreamError("knowledge base returned a malformed response") from exc

    @staticmethod
    def _cards(items: Any) -> list[Card]:
        try:
            return _CARDS.validate_python(items)
        except ValidationError as exc:
            raise UpstreamError("knowledge base returned a malformed card") from exc

    async def list_cards(self) -> list[Card]:
        """``GET /cards?limit=&offset=`` until a short page or ``total``; sorted by id."""
        cards: list[Card] = []
        offset = 0
        while True:
            payload = await self._json(
                self._http.get_json("/cards", params={"limit": self._page_size, "offset": offset})
            )
            page = self._cards(self._items(payload))
            cards.extend(page)
            offset += len(page)
            total = payload.get("total")
            if len(page) < self._page_size or (isinstance(total, int) and offset >= total):
                break
        return sorted(cards, key=lambda c: c.id)

    async def get_cards(self, ids: Sequence[str]) -> dict[str, Card]:
        """``POST /cards/batch {ids}``; ids M6 does not know are absent from the result."""
        unique = list(dict.fromkeys(ids))
        found: dict[str, Card] = {}
        for start in range(0, len(unique), self._batch_size):
            batch = unique[start : start + self._batch_size]
            payload = await self._json(self._http.post_json("/cards/batch", {"ids": batch}))
            for card in self._cards(self._items(payload)):
                if card.id in batch:
                    found[card.id] = card
        return found

    async def search(
        self, query: str, *, k: int = 10, kinds: Sequence[str] | None = None
    ) -> list[EvidenceHit]:
        """``POST /search`` (hybrid, reranked); extra fields of M6's items are ignored."""
        filters = {"kinds": list(kinds)} if kinds else {}
        body = {"query": query, "k": k, "filters": filters, "mode": "hybrid", "rerank": True}
        payload = await self._json(self._http.post_json("/search", body))
        items = self._items(payload)
        if not isinstance(items, list):
            raise UpstreamError("knowledge base returned a malformed response")
        try:
            return _HITS.validate_python(
                [{f: v for f, v in item.items() if f in EvidenceHit.model_fields} for item in items]
            )
        except (ValidationError, AttributeError) as exc:
            raise UpstreamError("knowledge base returned a malformed search hit") from exc


class UnconfiguredKnowledgeBase:
    """Stand-in when ``KB_URL`` is not set: every call fails cleanly and readiness is false."""

    async def list_cards(self) -> list[Card]:
        raise Unavailable("knowledge base is not configured (set KB_URL)")

    async def get_cards(self, ids: Sequence[str]) -> dict[str, Card]:
        raise Unavailable("knowledge base is not configured (set KB_URL)")
