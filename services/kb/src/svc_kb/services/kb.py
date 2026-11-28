"""M6 use cases: search with verbatim quotes, evidence and card lookups, stats."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from common.errors import NotFound, Unavailable
from common.ids import canonical_url
from common.schemas import EvidenceItem
from svc_kb.models.api import (
    CardsBatchResponse,
    CardsPage,
    EvidenceBatchResponse,
    EvidenceFull,
    IngestResponse,
    ReindexResponse,
    SearchRequest,
    SearchResponse,
)
from svc_kb.models.cards import CardIn, CardOut, EvidenceRow, KbStats
from svc_kb.services.chunking import chunk_text
from svc_kb.services.quotes import MAX_QUOTE_CHARS, best_quote
from svc_kb.services.search import ChunkIndex, IndexedCard, Mode, SearchFilters
from svc_kb.services.store import MAX_PAGE, Store


def _full(row: EvidenceRow, now: datetime) -> EvidenceFull:
    return EvidenceFull(
        evidence_id=row.evidence_id,
        card_id=row.card_id,
        card_name=row.card_name,
        source=row.source,
        kind=row.kind,
        url=row.url,
        text=row.text,
        quote=row.text[:MAX_QUOTE_CHARS],  # a prefix of the chunk, so it is a substring of it
        published_at=row.published_at,
        retrieved_at=now,
    )


class KbService:
    def __init__(self, store: Store, index: ChunkIndex) -> None:
        self._store = store
        self._index = index

    async def search(self, req: SearchRequest) -> SearchResponse:
        f = req.filters
        filters = SearchFilters(
            sources=f.sources,
            kinds=f.kinds,
            niches=f.niches,
            published_after=f.date_from,
            published_before=f.date_to,
        )
        mode: Mode = "hybrid+rerank" if req.mode == "hybrid" and req.rerank else req.mode
        hits = await self._index.search(req.query, mode=mode, k=req.k, filters=filters)
        now = datetime.now(UTC)
        items = [
            EvidenceItem(
                evidence_id=h.evidence_id,
                card_id=h.card_id,
                quote=best_quote(h.text, req.query),
                source=h.source,
                url=h.url,
                retrieved_at=now,
                score=h.score,
                rank=rank,
                card_name=h.card_name or None,
                published_at=h.published_at,
                niche=h.niche,
            )
            for rank, h in enumerate(hits, start=1)
        ]
        return SearchResponse(items=items)

    async def evidence_batch(self, ids: list[str]) -> EvidenceBatchResponse:
        rows = await self._store.evidence(ids)
        now = datetime.now(UTC)
        found = {r.evidence_id for r in rows}
        missing = [i for i in dict.fromkeys(ids) if i not in found]
        return EvidenceBatchResponse(items=[_full(r, now) for r in rows], missing=missing)

    async def evidence_one(self, evidence_id: str) -> EvidenceFull:
        rows = await self._store.evidence([evidence_id])
        if not rows:
            raise NotFound(f"no evidence {evidence_id[:80]!r}")
        return _full(rows[0], datetime.now(UTC))

    async def card(self, card_id: str) -> CardOut:
        got = await self._store.get_many([card_id])
        if not got:
            raise NotFound(f"no card {card_id[:80]!r}")
        return got[0]

    async def cards_batch(self, ids: list[str]) -> CardsBatchResponse:
        got = await self._store.get_many(ids)
        found = {c.id for c in got}
        return CardsBatchResponse(
            items=got, missing=[i for i in dict.fromkeys(ids) if i not in found]
        )

    async def cards_page(self, *, limit: int, offset: int) -> CardsPage:
        items, total = await self._store.list(limit=limit, offset=offset)
        return CardsPage(items=items, total=total)

    def _indexed(self, card: CardIn | CardOut, card_id: str) -> IndexedCard:
        return IndexedCard(
            card_id=card_id,
            url=canonical_url(card.url),
            source=card.source,
            kind=card.kind,
            niche_tags=list(card.niche_tags),
            published_at=card.published_at,
            chunks=chunk_text(card_id, card.text),
            card_name=card.name,
        )

    async def ingest(self, cards: list[CardIn]) -> IngestResponse:
        """Store each card, then index it. A re-seen unchanged card is not embedded again.

        If indexing fails after the store committed, the error surfaces (503) and ``reindex``
        repairs the gap; re-ingesting the same card is "unchanged" and would not repair it.
        """
        results = []
        for card in cards:
            result = await self._store.upsert(card)
            if result.changed:
                await self._index.upsert(self._indexed(card, result.card_id))
            results.append(result)
        return IngestResponse(results=results)

    async def reindex(self) -> ReindexResponse:
        """Rebuild the search index from the store (after a snapshot restore or a failed index)."""
        await self._index.ensure_collection()
        cards = chunks = offset = 0
        while True:
            page, total = await self._store.list(limit=MAX_PAGE, offset=offset)
            for card in page:
                indexed = self._indexed(card, card.id)
                await self._index.upsert(indexed)
                chunks += len(indexed.chunks)
            cards += len(page)
            offset += len(page)
            if not page or offset >= total:
                break
        return ReindexResponse(cards=cards, chunks=chunks)

    async def stats(self) -> KbStats:
        return await self._store.stats()

    async def ready(self) -> bool:
        return (
            await self._store.ping() and await self._index.ready() and self._index.models_loaded()
        )


class UnconfiguredKb:
    """Stand-in when DATABASE_URL or QDRANT_URL is missing: every call fails closed with 503."""

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def refuse(*_: object, **__: object) -> Any:
            if name == "ready":
                return False
            raise Unavailable("knowledge base is not configured (set DATABASE_URL and QDRANT_URL)")

        return refuse
