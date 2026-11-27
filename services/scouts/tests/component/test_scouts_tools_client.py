"""The M5 client against a faked ``POST /fetch`` (respx). It must never talk to anything else."""

import json
from typing import Any

import httpx
import pytest
import respx

from svc_scouts.clients.tools import ToolsClient
from svc_scouts.models.runs import ApiRequest
from svc_scouts.services.errors import (
    BudgetExhausted,
    FetchFailed,
    MalformedPayload,
    SourceRateLimited,
    UnsupportedRequest,
)

M5 = "http://tools:8000"


def ok_body(**over: Any) -> dict[str, Any]:
    return {
        "url": "https://hn.algolia.com/x",
        "status": 200,
        "cache_hit": False,
        "sanitiser": {"removed_patterns": 0, "flagged": False},
        "fetched_at": "2026-11-27T12:00:00Z",
        "json": {"hits": []},
        **over,
    }


def client(**kw: Any) -> ToolsClient:
    return ToolsClient(M5, internal_token="tok", max_retries=kw.pop("max_retries", 0), **kw)


@respx.mock
async def test_posts_the_documented_fetch_contract_with_the_internal_token() -> None:
    route = respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(200, json=ok_body()))
    page = await client().fetch(
        ApiRequest(url="https://hn.algolia.com/x"), source="hackernews", run_id="run_1"
    )
    sent = route.calls[0].request
    assert json.loads(sent.content) == {
        "url": "https://hn.algolia.com/x",
        "source": "hackernews",
        "purpose": "scout",
        "max_bytes": 2_000_000,
    }
    assert sent.headers["x-internal-token"] == "tok" and sent.headers["x-run-id"] == "run_1"
    assert page.status == 200 and page.json_ == {"hits": []} and not page.flagged


@respx.mock
async def test_the_sanitiser_flag_is_passed_up() -> None:
    respx.post(f"{M5}/fetch").mock(
        return_value=httpx.Response(
            200, json=ok_body(sanitiser={"removed_patterns": 2, "flagged": True})
        )
    )
    page = await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")
    assert page.flagged


@respx.mock
async def test_upstream_status_codes_are_returned_not_raised() -> None:
    respx.post(f"{M5}/fetch").mock(
        return_value=httpx.Response(200, json=ok_body(status=404, json=None, text="nope"))
    )
    page = await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")
    assert page.status == 404


@respx.mock
async def test_m5_429_becomes_a_rate_limit_stop() -> None:
    route = respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(SourceRateLimited):
        await _fast(client(max_retries=1)).fetch(
            ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r"
        )
    assert route.call_count == 2  # one bounded retry, no more


def _fast(c: ToolsClient) -> ToolsClient:
    c._client._backoff_s = 0.0  # keep the test quick
    return c


@respx.mock
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_m5_5xx_is_a_bounded_retry_then_a_fetch_failure(status: int) -> None:
    route = respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(FetchFailed):
        await _fast(client(max_retries=2)).fetch(
            ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r"
        )
    assert route.call_count == 3


@respx.mock
async def test_m5_timeout_is_a_fetch_failure() -> None:
    respx.post(f"{M5}/fetch").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(FetchFailed):
        await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")


@respx.mock
async def test_m5_unreachable_is_a_fetch_failure() -> None:
    respx.post(f"{M5}/fetch").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(FetchFailed):
        await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")


@respx.mock
async def test_a_refused_request_is_a_failure_with_status_403() -> None:
    respx.post(f"{M5}/fetch").mock(
        return_value=httpx.Response(
            403, json={"error": {"details": {"reason": "DOMAIN_NOT_ALLOWED"}}}
        )
    )
    with pytest.raises(FetchFailed) as caught:
        await client().fetch(ApiRequest(url="https://evil.example/x"), source="s", run_id="r")
    assert caught.value.status == 403


@respx.mock
async def test_m5_budget_exceeded_is_a_graceful_stop() -> None:
    respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(402, json={}))
    with pytest.raises(BudgetExhausted):
        await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")


@respx.mock
@pytest.mark.parametrize("body", [{}, {"status": "ok"}, {"url": "x"}, [], "text"])
async def test_an_unexpected_answer_shape_is_malformed(body: Any) -> None:
    respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(MalformedPayload):
        await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")


@respx.mock
async def test_optional_credentials_are_dropped_not_forwarded_when_m5_cannot_forward_them() -> None:
    route = respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(200, json=ok_body()))
    await client().fetch(
        ApiRequest(url="https://api.github.com/x", headers={"Authorization": "Bearer ghp_x"}),
        source="github",
        run_id="r",
    )
    assert b"ghp_x" not in route.calls[0].request.content  # the schema has no headers field


@respx.mock
@pytest.mark.parametrize(
    "request_",
    [
        ApiRequest(
            url="https://oauth.reddit.com/x",
            headers={"Authorization": "Bearer t"},
            auth_required=True,
        ),
        ApiRequest(url="https://api.producthunt.com/v2/api/graphql", method="POST", body="{}"),
    ],
)
async def test_calls_that_need_post_or_auth_are_refused_before_leaving_the_process(
    request_: ApiRequest,
) -> None:
    route = respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(200, json=ok_body()))
    with pytest.raises(UnsupportedRequest):
        await client().fetch(request_, source="s", run_id="r")
    assert route.call_count == 0


@respx.mock
async def test_with_forwarding_switched_on_the_extra_fields_are_sent() -> None:
    route = respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(200, json=ok_body()))
    await client(forward_auth=True).fetch(
        ApiRequest(
            url="https://api.producthunt.com/v2/api/graphql",
            method="POST",
            body="{}",
            headers={"Authorization": "Bearer t"},
            auth_required=True,
        ),
        source="producthunt",
        run_id="r",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["method"] == "POST" and sent["headers"] == {"Authorization": "Bearer t"}


@respx.mock
async def test_nothing_but_m5_is_ever_contacted() -> None:
    respx.post(f"{M5}/fetch").mock(return_value=httpx.Response(200, json=ok_body()))
    await client().fetch(ApiRequest(url="https://hn.algolia.com/x"), source="s", run_id="r")
    assert {str(c.request.url) for c in respx.calls} == {f"{M5}/fetch"}
