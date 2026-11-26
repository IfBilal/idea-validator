import httpx
import pytest
import respx
from common.context import parent_step_var, request_id_var, run_id_var
from common.errors import NotFound, RateLimited, UpstreamError, UpstreamTimeout
from common.http import ServiceClient

BASE = "http://kb.test"


def client(**kw: object) -> ServiceClient:
    return ServiceClient(BASE, timeout_s=1.0, backoff_s=0.0, **kw)  # type: ignore[arg-type]


@respx.mock
async def test_retries_503_then_succeeds_and_reports_retries() -> None:
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    response = await client().request("GET", "/x")
    assert response.json() == {"ok": 1}
    assert response.extensions["retries"] == 2
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_max_retries_with_upstream_error() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(503))
    with pytest.raises(UpstreamError):
        await client(max_retries=2).request("GET", "/x")
    assert route.call_count == 3


@respx.mock
async def test_timeouts_are_retried_then_raise_upstream_timeout() -> None:
    route = respx.get(f"{BASE}/x").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(UpstreamTimeout):
        await client().request("GET", "/x")
    assert route.call_count == 3


@respx.mock
async def test_429_is_retried_then_rate_limited() -> None:
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited):
        await client().request("GET", "/x")


@respx.mock
async def test_validation_and_not_found_are_never_retried() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(404))
    with pytest.raises(NotFound):
        await client().request("GET", "/x")
    assert route.call_count == 1


@respx.mock
async def test_plain_500_is_not_retried() -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(500))
    with pytest.raises(UpstreamError):
        await client().request("GET", "/x")
    assert route.call_count == 1


@respx.mock
async def test_trace_and_internal_token_headers_are_sent() -> None:
    route = respx.post(f"{BASE}/search").mock(return_value=httpx.Response(200, json={}))
    tokens = (request_id_var.set("req_1"), run_id_var.set("run_1"), parent_step_var.set(4))
    try:
        await client(internal_token="tok").post_json("/search", {"q": "x"})
    finally:
        request_id_var.reset(tokens[0])
        run_id_var.reset(tokens[1])
        parent_step_var.reset(tokens[2])
    sent = route.calls.last.request.headers
    assert sent["x-request-id"] == "req_1"
    assert sent["x-run-id"] == "run_1"
    assert sent["x-parent-step"] == "4"
    assert sent["x-internal-token"] == "tok"
