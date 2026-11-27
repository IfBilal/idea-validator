import json

import httpx
import pytest

from common.errors import NotFound, UpstreamError
from svc_critic.clients.knowledge_base import BATCH_SIZE, KnowledgeBaseClient

E1 = "card_8f21aaaa#c1"
E2 = "card_8f21aaaa#c2"


def make(
    transport: httpx.MockTransport, *, max_retries: int = 0, internal_token: str | None = None
) -> KnowledgeBaseClient:
    return KnowledgeBaseClient(
        "http://kb.test",
        timeout_s=1,
        max_retries=max_retries,
        internal_token=internal_token,
        backoff_s=0,
        transport=transport,
    )


def item(eid: str, text: str = "some text", name: str = "Notion") -> dict[str, str]:
    return {"evidence_id": eid, "card_id": eid.split("#")[0], "card_name": name, "text": text}


async def test_fetch_returns_found_items_keyed_by_id() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/evidence/batch"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"items": [item(E1)]})

    client = make(httpx.MockTransport(handler))
    found = await client.fetch_evidence([E1, E2])
    assert set(found) == {E1}
    assert found[E1].body == "some text"
    assert seen == [{"ids": [E1, E2]}]


async def test_duplicate_ids_are_requested_once() -> None:
    bodies: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"items": []})

    await make(httpx.MockTransport(handler)).fetch_evidence([E1, E1, E2, E1])
    assert bodies == [{"ids": [E1, E2]}]


async def test_empty_input_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    assert await make(httpx.MockTransport(handler)).fetch_evidence([]) == {}


async def test_unrequested_items_are_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [item(E1), item(E2)]})

    found = await make(httpx.MockTransport(handler)).fetch_evidence([E1])
    assert set(found) == {E1}


async def test_large_requests_are_split_into_batches() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["ids"]))
        return httpx.Response(200, json={"items": []})

    ids = [f"card_8f21aaaa#c{i}" for i in range(BATCH_SIZE + 1)]
    await make(httpx.MockTransport(handler)).fetch_evidence(ids)
    assert sizes == [BATCH_SIZE, 1]


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": []},
        {"items": "nope"},
        {"items": [{"evidence_id": "not-an-id"}]},
        ["a", "list"],
    ],
)
async def test_malformed_response_is_an_upstream_error(payload: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(UpstreamError):
        await make(httpx.MockTransport(handler)).fetch_evidence([E1])


async def test_non_json_response_is_an_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    with pytest.raises(UpstreamError):
        await make(httpx.MockTransport(handler)).fetch_evidence([E1])


async def test_upstream_5xx_is_retried_then_raised() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = KnowledgeBaseClient(
        "http://kb.test",
        timeout_s=1,
        max_retries=2,
        backoff_s=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(UpstreamError):
        await client.fetch_evidence([E1])
    assert calls == 3


async def test_client_error_is_mapped_and_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    with pytest.raises(NotFound):
        await make(httpx.MockTransport(handler), max_retries=2).fetch_evidence([E1])
    assert calls == 1


async def test_internal_token_is_sent() -> None:
    tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers.get("x-internal-token"))
        return httpx.Response(200, json={"items": []})

    await make(httpx.MockTransport(handler), internal_token="s3cret").fetch_evidence([E1])
    assert tokens == ["s3cret"]


async def test_is_ready_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ready"
        return httpx.Response(200, json={"status": "ready"})

    assert await make(httpx.MockTransport(handler)).is_ready() is True


@pytest.mark.parametrize("status", [503, 500, 404])
async def test_is_ready_false_on_error_status(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    assert await make(httpx.MockTransport(handler)).is_ready() is False


async def test_is_ready_false_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await make(httpx.MockTransport(handler)).is_ready() is False
