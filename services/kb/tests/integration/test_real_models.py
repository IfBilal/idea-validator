"""The real FastEmbed models end to end through ChunkIndex (downloads ~150 MB on first run)."""

import pytest
from qdrant_client import AsyncQdrantClient

from svc_kb.services.chunking import Chunk
from svc_kb.services.models import DENSE_DIM, FastEmbedModels
from svc_kb.services.search import ChunkIndex, IndexedCard

pytestmark = pytest.mark.integration

DOCS = {
    "card_0000000a": "Our invoicing tool for freelancers sends payment reminders automatically.",
    "card_0000000b": "A linter that catches mistakes in Dockerfiles before you build the image.",
    "card_0000000c": "Hiking trails near Lisbon with elevation profiles and weather forecasts.",
}


async def test_real_models_rank_the_semantically_right_card_first_in_every_mode() -> None:
    idx = ChunkIndex(
        AsyncQdrantClient(":memory:"), FastEmbedModels(), collection="r", dim=DENSE_DIM
    )
    await idx.ensure_collection()
    for cid, text in DOCS.items():
        await idx.upsert(
            IndexedCard(
                cid,
                f"https://x.example/{cid}",
                "github",
                "product",
                [],
                None,
                [Chunk(f"{cid}#c0", cid, 0, text)],
            )
        )
    for mode in ("vector", "hybrid", "hybrid+rerank"):
        hits = await idx.search("tool that checks container build files", mode=mode, k=3)
        assert hits[0].card_id == "card_0000000b", mode
    keyword = await idx.search("freelancers invoicing", mode="keyword", k=3)
    assert keyword[0].card_id == "card_0000000a"
