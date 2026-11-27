"""The HTTP API over real Postgres and in-memory Qdrant: what ingestion writes, M6 serves."""

from datetime import date

import httpx
import pytest
from pydantic import SecretStr
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from svc_kb.config import Settings
from svc_kb.main import build_app
from svc_kb.models.cards import CardIn
from svc_kb.repositories.cards import CardRepository
from svc_kb.services.chunking import chunk_text
from svc_kb.services.search import ChunkIndex, IndexedCard
from svc_kb.services.store import PgStore
from svc_kb.testing import DIM, FakeModels

pytestmark = pytest.mark.integration


async def test_ingested_card_is_searchable_citable_and_listed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    index = ChunkIndex(AsyncQdrantClient(":memory:"), FakeModels(), collection="pg", dim=DIM)
    await index.ensure_collection()
    card = CardIn(
        name="Dockerlint",
        summary="A CLI that lints Dockerfiles.",
        source="hackernews",
        kind="product",
        url="https://example.com/dockerlint",
        fetched_at=date(2026, 11, 20),
        published_at=date(2026, 11, 1),
        niche_tags=["devtools"],
        text="Dockerlint is a CLI. Exporting reports is slow on big repos. Pricing is fair.",
    )
    async with sessions() as session, session.begin():
        result = await CardRepository(session).upsert(card, chunk_text("card_00000000", card.text))
    await index.upsert(
        IndexedCard(
            result.card_id,
            "https://example.com/dockerlint",
            "hackernews",
            "product",
            ["devtools"],
            card.published_at,
            chunk_text(result.card_id, card.text),
            card_name=card.name,
        )
    )
    app = build_app(Settings(internal_token=SecretStr("t")), store=PgStore(sessions), index=index)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
        headers={"X-Internal-Token": "t"},
    ) as c:
        assert (await c.get("/ready")).status_code == 200
        hit = (await c.post("/search", json={"query": "exporting reports slow"})).json()["items"][0]
        assert (
            hit["card_id"] == result.card_id
            and hit["quote"] == "Exporting reports is slow on big repos."
        )
        ev = (await c.post("/evidence/batch", json={"ids": [hit["evidence_id"]]})).json()
        assert hit["quote"] in ev["items"][0]["text"], "M12 substring check passes"
        assert (await c.get("/cards")).json()["total"] == 1
        stats = (await c.get("/stats")).json()
        assert stats["cards"] == 1 and stats["snapshot_date"] == "2026-11-20"
