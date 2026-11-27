"""Hybrid chunk search on an in-memory Qdrant with deterministic fake models (docs/harness/10 section 6)."""

import hashlib
import math
import re
from datetime import date

import pytest
from qdrant_client import AsyncQdrantClient

from svc_kb.services.chunking import Chunk
from svc_kb.services.search import (
    ChunkIndex,
    IndexedCard,
    SearchFilters,
    SparseVec,
)

DIM = 64
WORD = re.compile(r"[a-z0-9]+")


def _h(word: str) -> int:
    return int.from_bytes(hashlib.md5(word.encode(), usedforsecurity=False).digest()[:4], "big")


class FakeModels:
    """Dense = hashed bag of words; sparse = word counts; rerank = shared-word count."""

    async def dense(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DIM
            for w in WORD.findall(t.lower()):
                v[_h(w) % DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    async def sparse(self, texts: list[str]) -> list[SparseVec]:
        out = []
        for t in texts:
            counts: dict[int, float] = {}
            for w in WORD.findall(t.lower()):
                counts[_h(w)] = counts.get(_h(w), 0.0) + 1.0
            out.append(SparseVec(indices=list(counts), values=list(counts.values())))
        return out

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        q = set(WORD.findall(query.lower()))
        return [float(len(q & set(WORD.findall(t.lower())))) for t in texts]


def card(
    card_id: str,
    text: str,
    *,
    source: str = "hackernews",
    kind: str = "product",
    tags: tuple[str, ...] = ("devtools",),
    published: date | None = date(2026, 10, 1),
) -> IndexedCard:
    return IndexedCard(
        card_id=card_id,
        url=f"https://example.com/{card_id}",
        source=source,
        kind=kind,
        niche_tags=list(tags),
        published_at=published,
        chunks=[Chunk(f"{card_id}#c0", card_id, 0, text)],
    )


@pytest.fixture
async def index() -> ChunkIndex:
    idx = ChunkIndex(AsyncQdrantClient(":memory:"), FakeModels(), collection="t", dim=DIM)
    await idx.ensure_collection()
    await idx.upsert(
        card("card_00000001", "dockerfile linter exporting reports is slow on big repos")
    )
    await idx.upsert(
        card(
            "card_00000002",
            "invoice generator for freelancers with stripe",
            source="github",
            kind="repo",
            tags=("fintech",),
            published=date(2025, 1, 5),
        )
    )
    await idx.upsert(
        card(
            "card_00000003",
            "pricing complaints about kubernetes dashboards",
            source="lobsters",
            kind="complaint",
            tags=("devtools", "k8s"),
            published=None,
        )
    )
    return idx


async def test_keyword_mode_finds_the_chunk_that_has_the_words(index: ChunkIndex) -> None:
    hits = await index.search("stripe invoice", mode="keyword", k=3)
    assert hits[0].evidence_id == "card_00000002#c0"
    assert hits[0].card_id == "card_00000002" and "stripe" in hits[0].text


async def test_hybrid_returns_the_best_chunk_first_and_carries_card_fields(
    index: ChunkIndex,
) -> None:
    hits = await index.search("dockerfile reports slow", mode="hybrid", k=3)
    top = hits[0]
    assert top.evidence_id == "card_00000001#c0"
    assert (top.source, top.kind, top.url) == (
        "hackernews",
        "product",
        "https://example.com/card_00000001",
    )
    assert top.published_at == date(2026, 10, 1)


async def test_rerank_mode_lets_the_cross_encoder_reorder_the_fused_list(index: ChunkIndex) -> None:
    hits = await index.search("kubernetes dashboards pricing", mode="hybrid+rerank", k=3)
    assert hits[0].evidence_id == "card_00000003#c0"
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


async def test_filters_restrict_by_source_kind_niche_and_dates(index: ChunkIndex) -> None:
    q = "dockerfile invoice kubernetes"
    by_source = await index.search(q, mode="hybrid", k=5, filters=SearchFilters(sources=["github"]))
    assert {h.card_id for h in by_source} == {"card_00000002"}
    by_kind = await index.search(q, mode="hybrid", k=5, filters=SearchFilters(kinds=["complaint"]))
    assert {h.card_id for h in by_kind} == {"card_00000003"}
    by_niche = await index.search(q, mode="hybrid", k=5, filters=SearchFilters(niches=["k8s"]))
    assert {h.card_id for h in by_niche} == {"card_00000003"}
    recent = await index.search(
        q, mode="hybrid", k=5, filters=SearchFilters(published_after=date(2026, 1, 1))
    )
    assert {h.card_id for h in recent} == {"card_00000001"}, (
        "undated cards cannot satisfy a date filter"
    )
    old = await index.search(
        q, mode="hybrid", k=5, filters=SearchFilters(published_before=date(2025, 12, 31))
    )
    assert {h.card_id for h in old} == {"card_00000002"}


async def test_reindexing_a_card_replaces_its_chunks_instead_of_duplicating(
    index: ChunkIndex,
) -> None:
    await index.upsert(card("card_00000001", "completely rewritten text about quantum widgets"))
    hits = await index.search("dockerfile", mode="keyword", k=5)
    assert all(h.card_id != "card_00000001" for h in hits)
    hits = await index.search("quantum widgets", mode="keyword", k=5)
    assert [h.evidence_id for h in hits if h.card_id == "card_00000001"] == ["card_00000001#c0"]
    assert await index.count() == 3


async def test_a_shorter_rewrite_drops_the_stale_trailing_chunks(index: ChunkIndex) -> None:
    two = card("card_00000009", "alpha beta")
    two.chunks.append(Chunk("card_00000009#c1", "card_00000009", 1, "gamma delta"))
    await index.upsert(two)
    assert await index.count() == 5
    await index.upsert(card("card_00000009", "alpha beta"))
    assert await index.count() == 4
    hits = await index.search("gamma delta", mode="keyword", k=5)
    assert "card_00000009#c1" not in {h.evidence_id for h in hits}


async def test_delete_card_removes_all_its_chunks(index: ChunkIndex) -> None:
    await index.delete_card("card_00000002")
    assert await index.count() == 2
    hits = await index.search("stripe invoice", mode="keyword", k=5)
    assert "card_00000002" not in {h.card_id for h in hits}


async def test_empty_query_and_bad_k_are_refused(index: ChunkIndex) -> None:
    with pytest.raises(ValueError, match="query"):
        await index.search("   ", mode="hybrid", k=5)
    with pytest.raises(ValueError, match="k"):
        await index.search("x", mode="hybrid", k=0)
    with pytest.raises(ValueError, match="k"):
        await index.search("x", mode="hybrid", k=101)


async def test_unknown_mode_is_refused(index: ChunkIndex) -> None:
    with pytest.raises(ValueError, match="mode"):
        await index.search("x", mode="magic", k=3)  # type: ignore[arg-type]


async def test_ensure_collection_is_idempotent_and_search_on_empty_is_empty() -> None:
    idx = ChunkIndex(AsyncQdrantClient(":memory:"), FakeModels(), collection="e", dim=DIM)
    await idx.ensure_collection()
    await idx.ensure_collection()
    assert await idx.search("anything", mode="hybrid", k=5) == []
