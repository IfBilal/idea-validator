"""HTTP behaviour with the fine-tuned model behind a llama.cpp server and hosted as fallback."""

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from svc_scorer.config import Settings
from svc_scorer.main import build_app

ROOT = Path(__file__).resolve().parents[4]
TOKEN = "test-internal-token"
ID1 = "card_8f21a3b4#c2"
HEADERS = {"X-Internal-Token": TOKEN}
GOOD = json.dumps(
    {"score": 3, "confidence": 0.6, "evidence_ids": [ID1], "reason": "One source complains."}
)
BODY = {
    "rubric_id": "demand_pain@v1",
    "idea": "A study planner",
    "evidence": [{"evidence_id": ID1, "quote": "notes export is slow"}],
    "features": {"n_sources": 3},
    "run_id": "run_1",
}


def _settings(*, hosted: bool = True) -> Settings:
    return Settings(
        internal_token=SecretStr(TOKEN),
        llm_url="http://tools.test" if hosted else None,
        local_llm_url="http://llama.test",
        rubrics_dir=ROOT / "rubrics",
        prompts_dir=ROOT / "prompts",
        llm_max_retries=0,
    )


def _server(replies: list[str], *, healthy: bool = True) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200 if healthy else 503, json={"status": "ok"})
        content = replies.pop(0) if replies else GOOD
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_ready_only_after_the_model_is_loaded_and_warm() -> None:
    cold = build_app(_settings(), local_transport=_server([], healthy=False))
    async with _client(cold) as client:
        assert (await client.get("/ready")).status_code == 503
    warm = build_app(_settings(), local_transport=_server([GOOD]))
    async with _client(warm) as client:
        assert (await client.get("/ready")).status_code == 200


async def test_score_is_served_by_the_local_model_and_tagged() -> None:
    app = build_app(_settings(), local_transport=_server([GOOD]), llm_transport=_server(["unused"]))
    async with _client(app) as client:
        body = (await client.post("/score", json=BODY, headers=HEADERS)).json()
    assert body["model"] == "scorer-lora@v1"
    assert body["fallback"] is False and body["score"] == 3
    assert "verdict" not in body


async def test_two_bad_local_replies_fall_back_to_hosted_and_are_counted() -> None:
    app = build_app(
        _settings(),
        local_transport=_server(["junk", "junk"]),
        llm_transport=_server([GOOD]),
    )
    async with _client(app) as client:
        body = (await client.post("/score", json=BODY, headers=HEADERS)).json()
        metrics = (await client.get("/metrics")).text
    assert body["model"] == "hosted-default" and body["fallback"] is True
    assert 'scorer_fallback_total{reason="invalid_output"} 1.0' in metrics


async def test_local_only_deployment_abstains_instead_of_failing() -> None:
    app = build_app(_settings(hosted=False), local_transport=_server(["junk", "junk"]))
    async with _client(app) as client:
        response = await client.post("/score", json=BODY, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["abstained"] is True
