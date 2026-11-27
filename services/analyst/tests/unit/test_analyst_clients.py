"""M6 stand-ins: in-memory fakes and the HTTP client (httpx.MockTransport), plus the store."""

import json
from datetime import date

import httpx
import pytest

from common.errors import Unavailable, UpstreamError
from svc_analyst.clients.fakes import InMemoryCardSource, InMemoryEvidenceSearch
from svc_analyst.clients.kb import KnowledgeBaseClient, UnconfiguredKnowledgeBase
from svc_analyst.fixtures import build_cards
from svc_analyst.models.cards import Card
from svc_analyst.models.domain import Snapshot
from svc_analyst.repositories.memory import InMemoryNicheStore

CARDS = build_cards()


def kb(handler: httpx.MockTransport) -> KnowledgeBaseClient:
    return KnowledgeBaseClient(
        "http://kb", timeout_s=2, max_retries=0, internal_token="tok", transport=handler
    )


# ---- fixtures meet the task's minimums -----------------------------------------------------------


def test_fixture_corpus_is_realistic_enough() -> None:
    assert len(CARDS) >= 60
    assert len({c.id for c in CARDS}) == len(CARDS)
    assert len({p for c in CARDS for p in c.all_platforms}) == 5
    months = {(c.published_at.year, c.published_at.month) for c in CARDS if c.published_at}
    assert len(months) >= 12
    assert {c.kind for c in CARDS} == {"product", "complaint", "request", "discussion"}


# ---- fakes -----------------------------------------------------------------------------------


async def test_in_memory_source_lists_sorted_and_gets_by_id() -> None:
    source = InMemoryCardSource(list(reversed(CARDS)))
    listed = await source.list_cards()
    assert [c.id for c in listed] == sorted(c.id for c in CARDS)
    got = await source.get_cards([CARDS[0].id, "card_deadbeef"])
    assert list(got) == [CARDS[0].id]
    assert await source.get_cards([]) == {}


async def test_in_memory_search_ranks_and_filters() -> None:
    search = InMemoryEvidenceSearch(CARDS)
    hits = await search.search("invoice billing for freelancers", k=5)
    assert 0 < len(hits) <= 5
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]
    by_id = {c.id: c for c in CARDS}
    assert "invoic" in by_id[hits[0].card_id].document.lower()
    only_products = await search.search("invoice billing", k=50, kinds=["product"])
    assert only_products and all(by_id[h.card_id].kind == "product" for h in only_products)
    assert all(h.evidence_id == f"{h.card_id}#c0" for h in hits)


async def test_in_memory_search_empty_query_and_no_match() -> None:
    search = InMemoryEvidenceSearch(CARDS)
    assert await search.search("", k=5) == []
    assert await search.search("zzzzqqq", k=5) == []
    assert await InMemoryEvidenceSearch([]).search("anything", k=5) == []


# ---- http client -----------------------------------------------------------------------------


def card_json(card: Card) -> dict[str, object]:
    data: dict[str, object] = json.loads(card.model_dump_json())
    return data


async def test_client_pages_through_cards_and_sends_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        page = CARDS[offset : offset + limit]
        return httpx.Response(
            200, json={"items": [card_json(c) for c in page], "total": len(CARDS)}
        )

    client = KnowledgeBaseClient(
        "http://kb",
        timeout_s=2,
        max_retries=0,
        internal_token="tok",
        page_size=40,
        transport=httpx.MockTransport(handler),
    )
    cards = await client.list_cards()
    assert [c.id for c in cards] == [c.id for c in CARDS]
    assert len(seen) == 3  # 86 cards / 40 per page
    assert all(r.headers["x-internal-token"] == "tok" for r in seen)
    await client.aclose()


async def test_client_list_cards_with_exact_page_boundary_and_empty_kb() -> None:
    def exact(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        page = CARDS[:4][offset : offset + 2]
        return httpx.Response(200, json={"items": [card_json(c) for c in page], "total": 4})

    client = KnowledgeBaseClient(
        "http://kb", timeout_s=2, max_retries=0, page_size=2, transport=httpx.MockTransport(exact)
    )
    assert len(await client.list_cards()) == 4

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "total": 0})

    client = kb(httpx.MockTransport(empty))
    assert await client.list_cards() == []


async def test_client_get_cards_batches_and_ignores_unknown() -> None:
    posted: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["ids"]
        posted.append(ids)
        known = [card_json(c) for c in CARDS if c.id in ids]
        return httpx.Response(200, json={"items": known})

    client = KnowledgeBaseClient(
        "http://kb",
        timeout_s=2,
        max_retries=0,
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )
    ids = [CARDS[0].id, CARDS[1].id, CARDS[2].id, "card_deadbeef", CARDS[0].id]
    got = await client.get_cards(ids)
    assert set(got) == {CARDS[0].id, CARDS[1].id, CARDS[2].id}
    assert [len(b) for b in posted] == [2, 2]  # de-duplicated, then batched
    assert await client.get_cards([]) == {}


async def test_client_search_maps_hits_and_sends_filters() -> None:
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "evidence_id": "card_8f21a3b4#c2",
                        "card_id": "card_8f21a3b4",
                        "quote": "slow export",
                        "source": "reddit",
                        "published_at": "2026-05-01",
                        "score": 0.91,
                        "rank": 1,
                        "niche": "notes",
                        "card_name": "x",
                        "url": "https://e.com",
                        "retrieved_at": "2026-06-01T00:00:00Z",
                    }
                ],
                "index_version": 3,
                "cache_hit": False,
            },
        )

    client = kb(httpx.MockTransport(handler))
    hits = await client.search("slow export", k=7, kinds=["product"])
    assert sent["query"] == "slow export"
    assert sent["k"] == 7
    assert sent["filters"] == {"kinds": ["product"]}
    assert sent["mode"] == "hybrid"
    assert hits[0].card_id == "card_8f21a3b4"
    assert hits[0].published_at == date(2026, 5, 1)
    assert hits[0].score == 0.91


@pytest.mark.parametrize("body", [{"nope": 1}, {"items": [{"evidence_id": "bad"}]}, {"items": 3}])
async def test_client_rejects_malformed_search_responses(body: object) -> None:
    client = kb(httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(UpstreamError):
        await client.search("q", k=3)


async def test_client_rejects_non_json_and_malformed_cards() -> None:
    client = kb(httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(UpstreamError):
        await client.list_cards()
    client = kb(httpx.MockTransport(lambda r: httpx.Response(200, json={"items": [{"id": "x"}]})))
    with pytest.raises(UpstreamError):
        await client.list_cards()
    client = kb(httpx.MockTransport(lambda r: httpx.Response(200, json={"items": [{"id": "x"}]})))
    with pytest.raises(UpstreamError):
        await client.get_cards(["card_00000001"])


async def test_client_maps_upstream_failures() -> None:
    client = kb(httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(Exception, match=r"503|unavailable|returned"):
        await client.list_cards()


async def test_client_ping_reflects_readiness() -> None:
    ok = kb(httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ready"})))
    assert await ok.ping() is True
    down = kb(httpx.MockTransport(lambda r: httpx.Response(503)))
    assert await down.ping() is False


# ---- store -----------------------------------------------------------------------------------


async def test_store_round_trips_and_starts_empty() -> None:
    store = InMemoryNicheStore()
    assert await store.load() is None
    snap = Snapshot(as_of=date(2026, 6, 1), niches=[], gaps=[], unassigned=[])
    await store.save(snap)
    assert await store.load() == snap
    newer = Snapshot(as_of=date(2026, 7, 1), niches=[], gaps=[], unassigned=[])
    await store.save(newer)
    assert await store.load() == newer


async def test_unconfigured_knowledge_base_fails_closed() -> None:
    none = UnconfiguredKnowledgeBase()
    with pytest.raises(Unavailable):
        await none.list_cards()
    with pytest.raises(Unavailable):
        await none.get_cards(["card_00000001"])
