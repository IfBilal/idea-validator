"""ServiceClient.get_json_capped: bounded reads, no retries, typed errors."""

import httpx
import pytest

from common.errors import NotFound, UpstreamError, UpstreamTimeout
from common.http import ServiceClient


def client(handler: httpx.MockTransport) -> ServiceClient:
    return ServiceClient("http://svc.test", timeout_s=1, backoff_s=0, transport=handler)


async def test_a_small_json_body_is_parsed() -> None:
    c = client(httpx.MockTransport(lambda r: httpx.Response(200, json={"a": 1})))
    assert await c.get_json_capped("/x", max_bytes=100) == {"a": 1}


async def test_exactly_at_the_cap_passes_and_one_over_fails() -> None:
    body = b'{"k":"' + b"x" * 10 + b'"}'
    ok = client(httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    assert await ok.get_json_capped("/x", max_bytes=len(body)) == {"k": "x" * 10}
    with pytest.raises(UpstreamError):
        await ok.get_json_capped("/x", max_bytes=len(body) - 1)


async def test_a_5xx_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={})

    with pytest.raises(UpstreamError):
        await client(httpx.MockTransport(handler)).get_json_capped("/x", max_bytes=100)
    assert len(calls) == 1


async def test_status_and_transport_errors_are_typed() -> None:
    missing = client(httpx.MockTransport(lambda r: httpx.Response(404, json={})))
    with pytest.raises(NotFound):
        await missing.get_json_capped("/x", max_bytes=100)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(UpstreamTimeout):
        await client(httpx.MockTransport(slow)).get_json_capped("/x", max_bytes=100)


async def test_a_body_that_is_not_json_raises_value_error() -> None:
    c = client(httpx.MockTransport(lambda r: httpx.Response(200, content=b"nope")))
    with pytest.raises(ValueError, match="Expecting"):
        await c.get_json_capped("/x", max_bytes=100)
