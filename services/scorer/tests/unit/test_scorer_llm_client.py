"""LlmClient: request shape sent to M5 and response-envelope handling."""

import json
from collections.abc import Callable

import httpx
import pytest

from common.errors import UpstreamError, UpstreamTimeout
from svc_scorer.clients.llm import LlmClient


def _client(handler: Callable[[httpx.Request], httpx.Response], **kw: object) -> LlmClient:
    return LlmClient(
        "http://tools.test",
        model="hosted-default",
        timeout_s=1,
        max_retries=0,
        internal_token="tok",
        transport=httpx.MockTransport(handler),
        **kw,
    )


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_complete_posts_openai_style_request_and_returns_content() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok('{"score": 1}')

    out = await _client(handler).complete(run_id="run_1", system="SYS", user="USR", max_tokens=300)
    assert out == '{"score": 1}'
    req = seen[0]
    assert req.url.path == "/llm/v1/chat/completions"
    assert req.headers["x-internal-token"] == "tok"
    body = json.loads(req.content)
    assert body["run_id"] == "run_1"
    assert body["agent"] == "scorer"
    assert body["model"] == "hosted-default"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 300
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


async def test_top_level_content_envelope_is_accepted() -> None:
    client = _client(lambda _: httpx.Response(200, json={"content": "hi"}))
    assert await client.complete(run_id="r", system="s", user="u", max_tokens=1) == "hi"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": 5}}]},
    ],
)
async def test_malformed_envelope_is_an_upstream_error(payload: dict[str, object]) -> None:
    client = _client(lambda _: httpx.Response(200, json=payload))
    with pytest.raises(UpstreamError):
        await client.complete(run_id="r", system="s", user="u", max_tokens=1)


async def test_non_json_body_is_an_upstream_error() -> None:
    client = _client(lambda _: httpx.Response(200, text="<html>"))
    with pytest.raises(UpstreamError):
        await client.complete(run_id="r", system="s", user="u", max_tokens=1)


async def test_timeout_maps_to_upstream_timeout() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(UpstreamTimeout):
        await _client(handler).complete(run_id="r", system="s", user="u", max_tokens=1)


async def test_server_error_maps_to_upstream_error() -> None:
    client = _client(lambda _: httpx.Response(500))
    with pytest.raises(UpstreamError):
        await client.complete(run_id="r", system="s", user="u", max_tokens=1)


async def test_ping_true_when_healthy_false_when_down() -> None:
    assert await _client(lambda _: httpx.Response(200, json={"status": "ok"})).ping() is True
    assert await _client(lambda _: httpx.Response(503)).ping() is False
