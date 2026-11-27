"""Chunk index over Qdrant: dense + sparse vectors, RRF fusion, optional cross-encoder rerank.

The embedding and rerank models sit behind the ``Models`` protocol, so tests use small fakes and
production uses FastEmbed (``svc_kb.services.models``). Chunks are the unit that is indexed and
cited; a point id is a UUID derived from the evidence id, so re-indexing a card overwrites in place.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol

from qdrant_client import AsyncQdrantClient, models

from svc_kb.services.chunking import Chunk

Mode = Literal["vector", "keyword", "hybrid", "hybrid+rerank"]
MODES: tuple[str, ...] = ("vector", "keyword", "hybrid", "hybrid+rerank")
MAX_K = 100
PREFETCH = 50  # candidates per leg and after fusion (docs/harness/10 section 6)
_NS = uuid.UUID("6f1c3a0e-5d1b-4a43-9b0c-2a7e0f4d9c11")


@dataclass(frozen=True)
class SparseVec:
    indices: list[int]
    values: list[float]


class Models(Protocol):
    async def dense(self, texts: list[str]) -> list[list[float]]: ...
    async def sparse(self, texts: list[str]) -> list[SparseVec]: ...
    async def rerank(self, query: str, texts: list[str]) -> list[float]: ...


@dataclass
class IndexedCard:
    card_id: str
    url: str
    source: str
    kind: str
    niche_tags: list[str]
    published_at: date | None
    chunks: list[Chunk]
    card_name: str = ""


@dataclass(frozen=True)
class SearchFilters:
    sources: Sequence[str] | None = None
    kinds: Sequence[str] | None = None
    niches: Sequence[str] | None = None
    published_after: date | None = None
    published_before: date | None = None


@dataclass(frozen=True)
class Hit:
    evidence_id: str
    card_id: str
    text: str
    score: float
    source: str
    kind: str
    url: str
    published_at: date | None
    card_name: str = ""
    niche: str | None = None


def _point_id(evidence_id: str) -> str:
    return str(uuid.uuid5(_NS, evidence_id))


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _filter(f: SearchFilters | None) -> models.Filter | None:
    if f is None:
        return None
    must: list[models.Condition] = []
    for key, values in (("source", f.sources), ("kind", f.kinds), ("niche_tags", f.niches)):
        if values:
            must.append(models.FieldCondition(key=key, match=models.MatchAny(any=values)))
    if f.published_after or f.published_before:
        must.append(
            models.FieldCondition(
                key="published_ts",
                range=models.Range(
                    gte=_epoch(f.published_after) if f.published_after else None,
                    lte=_epoch(f.published_before) if f.published_before else None,
                ),
            )
        )
    return models.Filter(must=must) if must else None


class ChunkIndex:
    def __init__(
        self, client: AsyncQdrantClient, models_: Models, *, collection: str, dim: int
    ) -> None:
        self._c = client
        self._m = models_
        self._collection = collection
        self._dim = dim

    async def ensure_collection(self) -> None:
        if await self._c.collection_exists(self._collection):
            return
        await self._c.create_collection(
            self._collection,
            vectors_config={
                "dense": models.VectorParams(size=self._dim, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )
        for key in ("source", "kind", "niche_tags", "card_id"):
            await self._c.create_payload_index(
                self._collection, key, models.PayloadSchemaType.KEYWORD
            )
        await self._c.create_payload_index(
            self._collection, "published_ts", models.PayloadSchemaType.INTEGER
        )

    async def ready(self) -> bool:
        return await self._c.collection_exists(self._collection)

    async def count(self) -> int:
        return (await self._c.count(self._collection, exact=True)).count

    async def delete_card(self, card_id: str) -> None:
        await self._c.delete(
            self._collection,
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="card_id", match=models.MatchValue(value=card_id))
                    ]
                )
            ),
        )

    async def upsert(self, card: IndexedCard) -> None:
        """Replace everything indexed for the card: no stale trailing chunks survive a rewrite."""
        await self.delete_card(card.card_id)
        if not card.chunks:
            return
        texts = [c.text for c in card.chunks]
        dense = await self._m.dense(texts)
        sparse = await self._m.sparse(texts)
        ts = _epoch(card.published_at) if card.published_at else None
        points = [
            models.PointStruct(
                id=_point_id(c.evidence_id),
                vector={
                    "dense": d,
                    "sparse": models.SparseVector(indices=s.indices, values=s.values),
                },
                payload={
                    "evidence_id": c.evidence_id,
                    "card_id": card.card_id,
                    "text": c.text,
                    "url": card.url,
                    "card_name": card.card_name,
                    "source": card.source,
                    "kind": card.kind,
                    "niche_tags": card.niche_tags,
                    "published_at": card.published_at.isoformat() if card.published_at else None,
                    "published_ts": ts,
                },
            )
            for c, d, s in zip(card.chunks, dense, sparse, strict=True)
        ]
        await self._c.upsert(self._collection, points, wait=True)

    async def search(
        self, query: str, *, mode: Mode, k: int, filters: SearchFilters | None = None
    ) -> list[Hit]:
        if not query.strip():
            raise ValueError("query is empty")
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be 1..{MAX_K}, got {k}")
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; use one of {MODES}")
        flt = _filter(filters)
        limit = max(k, PREFETCH) if mode == "hybrid+rerank" else k
        points = await self._query(query, mode, limit, flt)
        hits = [_hit(p) for p in points]
        if mode == "hybrid+rerank" and hits:
            scores = await self._m.rerank(query, [h.text for h in hits])
            hits = sorted(
                (Hit(**{**h.__dict__, "score": s}) for h, s in zip(hits, scores, strict=True)),
                key=lambda h: h.score,
                reverse=True,
            )
        return hits[:k]

    async def _query(
        self, query: str, mode: str, limit: int, flt: models.Filter | None
    ) -> list[models.ScoredPoint]:
        dense = (await self._m.dense([query]))[0]
        sp = (await self._m.sparse([query]))[0]
        sparse_q = models.SparseVector(indices=sp.indices, values=sp.values)
        if mode == "vector":
            res = await self._c.query_points(
                self._collection,
                query=dense,
                using="dense",
                query_filter=flt,
                limit=limit,
                with_payload=True,
            )
        elif mode == "keyword":
            res = await self._c.query_points(
                self._collection,
                query=sparse_q,
                using="sparse",
                query_filter=flt,
                limit=limit,
                with_payload=True,
            )
        else:
            res = await self._c.query_points(
                self._collection,
                prefetch=[
                    models.Prefetch(query=dense, using="dense", filter=flt, limit=PREFETCH),
                    models.Prefetch(query=sparse_q, using="sparse", filter=flt, limit=PREFETCH),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        return res.points


def _hit(p: models.ScoredPoint) -> Hit:
    pl = p.payload or {}
    published = pl.get("published_at")
    return Hit(
        evidence_id=pl["evidence_id"],
        card_id=pl["card_id"],
        text=pl["text"],
        score=float(p.score),
        source=pl["source"],
        kind=pl["kind"],
        url=pl["url"],
        published_at=date.fromisoformat(published) if published else None,
        card_name=pl.get("card_name", ""),
        niche=(pl.get("niche_tags") or [None])[0],
    )
