"""The llama.cpp server client: constrained JSON, warm-up readiness, error mapping."""

import json
from typing import Any

import httpx
import pytest

from common.errors import UpstreamError, UpstreamTimeout
from svc_scorer.clients.local import SCORE_JSON_SCHEMA, LocalLlamaClient

REPLY = '{"score": 3, "confidence": 0.5, "evidence_ids": [], "reason": "r"}'


def _client(handler: Any) -> LocalLlamaClient:
    return LocalLlamaClient(
        "http://llama.test",
        model="scorer-lora@v1",
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )


async def test_posts_openai_format_with_the_json_schema_and_zero_temperature() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": REPLY}}]})

    text = await _client(handler).complete(run_id="r", system="s", user="u", max_tokens=400)
    assert text == REPLY
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions"
    assert body["temperature"] == 0 and body["max_tokens"] == 400
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCORE_JSON_SCHEMA
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_schema_is_exactly_the_four_keys_and_has_no_verdict() -> None:
    assert set(SCORE_JSON_SCHEMA["properties"]) == {"score", "confidence", "evidence_ids", "reason"}
    assert SCORE_JSON_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize("status", [500, 503])
async def test_server_errors_map_to_upstream_error(status: int) -> None:
    c = _client(lambda _r: httpx.Response(status, text="boom"))
    with pytest.raises(UpstreamError):
        await c.complete(run_id="r", system="s", user="u", max_tokens=10)


async def test_timeouts_map_to_upstream_timeout() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(UpstreamTimeout):
        await _client(handler).complete(run_id="r", system="s", user="u", max_tokens=10)


async def test_connection_refused_maps_to_upstream_error() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(UpstreamError):
        await _client(handler).complete(run_id="r", system="s", user="u", max_tokens=10)


async def test_malformed_body_is_an_upstream_error() -> None:
    c = _client(lambda _r: httpx.Response(200, json={"nope": 1}))
    with pytest.raises(UpstreamError):
        await c.complete(run_id="r", system="s", user="u", max_tokens=10)


async def test_ready_needs_health_and_one_successful_warm_up_completion() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"choices": [{"message": {"content": REPLY}}]})

    c = _client(handler)
    assert await c.ready() is True
    assert await c.ready() is True
    assert calls.count("/v1/chat/completions") == 1  # the warm-up runs once, then is remembered


async def test_not_ready_while_the_model_is_loading() -> None:
    c = _client(lambda _r: httpx.Response(503, json={"error": "loading model"}))
    assert await c.ready() is False


async def test_not_ready_when_the_warm_up_completion_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(500, text="oom")

    assert await _client(handler).ready() is False
