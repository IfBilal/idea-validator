"""M6 HTTP contract through the real app, router, search index and quote extraction."""

from datetime import date, datetime

import httpx
import pytest
from pydantic import SecretStr
from qdrant_client import AsyncQdrantClient

from common.schemas import EvidenceItem
from svc_kb.config import Settings
from svc_kb.main import build_app
from svc_kb.models.cards import CardOut
from svc_kb.services.chunking import Chunk
from svc_kb.services.search import ChunkIndex, IndexedCard
from svc_kb.testing import DIM, FakeModels, InMemoryStore

TOKEN = "t0ken"
AUTH = {"X-Internal-Token": TOKEN}
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=None)

DOCS = {
    "card_00000001": (
        "Dockerlint",
        "hackernews",
        "product",
        ["devtools"],
        date(2026, 10, 1),
        "Dockerlint is a CLI. Exporting reports is slow on big repos. Pricing is fair.",
    ),
    "card_00000002": (
        "Invoicely",
        "github",
        "repo",
        ["fintech"],
        date(2025, 3, 2),
        "Invoice generator for freelancers with stripe support.",
    ),
    "card_00000003": (
        "K8s pricing rant",
        "lobsters",
        "complaint",
        ["devtools", "k8s"],
        None,
        "Kubernetes dashboards have confusing pricing. I cancelled after a month.",
    ),
}


def _card(cid: str) -> CardOut:
    name, source, kind, tags, published, text = DOCS[cid]
    return CardOut.model_validate(
        {
            "id": cid,
            "name": name,
            "summary": text[:80],
            "niche_tags": tags,
            "stage": "unknown",
            "source": source,
            "kind": kind,
            "url": f"https://example.com/{cid}",
            "published_at": published,
            "fetched_at": date(2026, 11, 20),
            "text": text,
            "content_hash": "0" * 64,
            "platforms": [source],
            "first_seen_at": datetime(2026, 11, 20),
            "last_seen_at": datetime(2026, 11, 20),
        }
    )


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    index = ChunkIndex(AsyncQdrantClient(":memory:"), FakeModels(), collection="c", dim=DIM)
    await index.ensure_collection()
    chunks = {}
    for cid, (name, source, kind, tags, published, text) in DOCS.items():
        ch = Chunk(f"{cid}#c0", cid, 0, text)
        chunks[ch.evidence_id] = text
        await index.upsert(
            IndexedCard(
                cid,
                f"https://example.com/{cid}",
                source,
                kind,
                tags,
                published,
                [ch],
                card_name=name,
            )
        )
    store = InMemoryStore([_card(c) for c in DOCS], chunks)
    app = build_app(Settings(internal_token=SecretStr(TOKEN)), store=store, index=index)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=AUTH
    ) as c:
        yield c


async def test_every_endpoint_needs_the_internal_token(client: httpx.AsyncClient) -> None:
    client.headers.pop("X-Internal-Token")
    for method, path in [
        ("get", "/stats"),
        ("get", "/cards"),
        ("get", "/cards/card_00000001"),
        ("get", "/evidence/card_00000001#c0"),
        ("post", "/search"),
        ("post", "/cards/batch"),
        ("post", "/evidence/batch"),
    ]:
        r = await getattr(client, method)(path)
        assert r.status_code == 401, path
    client.headers["X-Internal-Token"] = "wrong"
    assert (await client.get("/stats")).status_code == 401


async def test_search_returns_valid_evidence_items_with_verbatim_quotes(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/search", json={"query": "exporting reports slow", "k": 3, "run_id": "run_1"}
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["evidence_id"] == "card_00000001#c0" and items[0]["rank"] == 1
    for raw in items:
        EvidenceItem.model_validate(raw)  # the shared schema accepts every item
        assert len(raw["quote"]) <= 300
    assert items[0]["quote"] == "Exporting reports is slow on big repos."
    assert items[0]["card_name"] == "Dockerlint" and items[0]["niche"] == "devtools"
    assert items[0]["quote"] in DOCS["card_00000001"][5]


async def test_search_filters_modes_and_the_niche_alias(client: httpx.AsyncClient) -> None:
    body = {"query": "pricing invoice dockerlint", "k": 5}
    only = await client.post("/search", json=body | {"filters": {"kinds": ["complaint"]}})
    assert {i["card_id"] for i in only.json()["items"]} == {"card_00000003"}
    alias = await client.post("/search", json=body | {"filters": {"niche": "fintech"}})
    assert {i["card_id"] for i in alias.json()["items"]} == {"card_00000002"}
    dated = await client.post("/search", json=body | {"filters": {"date_from": "2026-01-01"}})
    assert {i["card_id"] for i in dated.json()["items"]} == {"card_00000001"}
    for mode in ("vector", "keyword", "hybrid"):
        r = await client.post("/search", json=body | {"mode": mode, "rerank": False})
        assert r.status_code == 200 and r.json()["items"], mode


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "x", "k": 0},
        {"query": "x", "k": 101},
        {"query": "x", "mode": "magic"},
        {"query": "x", "filters": {"kinds": ["rant"]}},
        {"query": "x", "extra": 1},
        {"query": "x", "filters": {"date_from": "2026-02-01", "date_to": "2026-01-01"}},
    ],
)
async def test_search_rejects_bad_requests(client: httpx.AsyncClient, body: dict) -> None:  # type: ignore[type-arg]
    assert (await client.post("/search", json=body)).status_code == 422


async def test_evidence_batch_returns_found_chunks_and_names_the_missing(
    client: httpx.AsyncClient,
) -> None:
    ids = ["card_00000002#c0", "card_00000009#c0", "garbage"]
    r = await client.post("/evidence/batch", json={"ids": ids})
    body = r.json()
    assert [i["evidence_id"] for i in body["items"]] == ["card_00000002#c0"]
    assert (
        body["items"][0]["text"] == DOCS["card_00000002"][5]
        and body["items"][0]["card_name"] == "Invoicely"
    )
    assert body["missing"] == ["card_00000009#c0", "garbage"]
    assert (
        await client.post("/evidence/batch", json={"ids": ["card_00000002#c0"] * 201})
    ).status_code == 422


async def test_evidence_by_id_is_404_when_unknown(client: httpx.AsyncClient) -> None:
    ok = await client.get("/evidence/card_00000003%23c0")
    assert ok.status_code == 200 and ok.json()["quote"] in ok.json()["text"]
    missing = await client.get("/evidence/card_00000003%23c9")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


async def test_cards_endpoints_follow_the_m7_contract(client: httpx.AsyncClient) -> None:
    page = (await client.get("/cards", params={"limit": 2, "offset": 0})).json()
    assert page["total"] == 3 and [c["id"] for c in page["items"]] == [
        "card_00000001",
        "card_00000002",
    ]
    assert page["items"][0]["platforms"] == ["hackernews"]
    rest = (await client.get("/cards", params={"limit": 2, "offset": 2})).json()
    assert [c["id"] for c in rest["items"]] == ["card_00000003"]
    for bad in ({"limit": 0}, {"limit": 501}, {"offset": -1}):
        assert (await client.get("/cards", params=bad)).status_code == 422
    one = await client.get("/cards/card_00000002")
    assert one.status_code == 200 and one.json()["name"] == "Invoicely"
    assert (await client.get("/cards/card_0000ffff")).status_code == 404
    batch = (
        await client.post("/cards/batch", json={"ids": ["card_00000003", "card_0000ffff"]})
    ).json()
    assert [c["id"] for c in batch["items"]] == ["card_00000003"] and batch["missing"] == [
        "card_0000ffff"
    ]


async def test_stats_carry_the_snapshot_date(client: httpx.AsyncClient) -> None:
    body = (await client.get("/stats")).json()
    assert body["cards"] == 3 and body["snapshot_date"] == "2026-11-20"
    assert body["by_source"] == {"hackernews": 1, "github": 1, "lobsters": 1}


async def test_an_unconfigured_service_fails_closed() -> None:
    app = build_app(Settings(internal_token=SecretStr(TOKEN)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=AUTH
    ) as c:
        assert (await c.get("/ready")).status_code == 503
        assert (await c.get("/stats")).status_code == 503
        assert (await c.post("/search", json={"query": "x"})).status_code == 503
