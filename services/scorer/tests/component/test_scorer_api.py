"""HTTP behaviour of the scorer: auth, schemas, error envelope, readiness, metrics."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from svc_scorer.config import Settings
from svc_scorer.main import build_app

ROOT = Path(__file__).resolve().parents[4]
TOKEN = "test-internal-token"
ID1 = "card_8f21a3b4#c2"
HEADERS = {"X-Internal-Token": TOKEN}


def _settings() -> Settings:
    return Settings(
        internal_token=SecretStr(TOKEN),
        llm_url="http://tools.test",
        rubrics_dir=ROOT / "rubrics",
        prompts_dir=ROOT / "prompts",
        llm_max_retries=0,
    )


def _llm_transport(replies: list[str], seen: list[httpx.Request] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": replies.pop(0)}}]})

    return httpx.MockTransport(handler)


def _app(transport: httpx.AsyncBaseTransport) -> FastAPI:
    return build_app(_settings(), llm_transport=transport)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rubric_id": "demand_pain@v1",
        "idea": "A study planner",
        "evidence": [{"evidence_id": ID1, "quote": "notes export is slow"}],
        "features": {"n_sources": 3},
        "run_id": "run_1",
    }
    body.update(over)
    return body


GOOD = json.dumps(
    {"score": 3, "confidence": 0.6, "evidence_ids": [ID1], "reason": "One source complains."}
)


async def test_score_requires_the_internal_token() -> None:
    async with _client(_app(_llm_transport([GOOD]))) as client:
        assert (await client.post("/score", json=_body())).status_code == 401
        bad = await client.post("/score", json=_body(), headers={"X-Internal-Token": "nope"})
        assert bad.status_code == 401
        assert bad.json()["error"]["code"] == "UNAUTHORIZED"


async def test_score_returns_a_dimension_score() -> None:
    seen: list[httpx.Request] = []
    async with _client(_app(_llm_transport([GOOD], seen))) as client:
        response = await client.post("/score", json=_body(), headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["dimension"] == "demand_pain"
    assert body["score"] == 3
    assert body["evidence_ids"] == [ID1]
    assert body["rubric_version"] == "demand_pain@v1"
    assert body["model"] == "hosted-default"
    assert "verdict" not in body
    assert seen[0].headers["x-internal-token"] == TOKEN


@pytest.mark.parametrize(
    "bad",
    [
        _body(extra_field=1),
        _body(rubric_id="Demand Pain"),
        _body(idea="x" * 4001),
        _body(evidence=[{"evidence_id": "nope", "quote": "q"}]),
        {k: v for k, v in _body().items() if k != "run_id"},
    ],
)
async def test_invalid_requests_get_the_error_envelope(bad: dict[str, Any]) -> None:
    async with _client(_app(_llm_transport([GOOD]))) as client:
        response = await client.post("/score", json=bad, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_unknown_rubric_is_404() -> None:
    async with _client(_app(_llm_transport([GOOD]))) as client:
        response = await client.post(
            "/score", json=_body(rubric_id="demand_pain@v99"), headers=HEADERS
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_unknown_dimension_is_422() -> None:
    async with _client(_app(_llm_transport([GOOD]))) as client:
        response = await client.post("/score", json=_body(rubric_id="verdict@v1"), headers=HEADERS)
    assert response.status_code == 422


async def test_invalid_model_output_gives_abstention_and_counts_fallback() -> None:
    app = _app(_llm_transport(["garbage", "garbage"]))
    async with _client(app) as client:
        response = await client.post("/score", json=_body(), headers=HEADERS)
        metrics = (await client.get("/metrics")).text
    body = response.json()
    assert (body["score"], body["confidence"], body["fallback"]) == (None, 0, True)
    assert 'scorer_fallback_total{reason="invalid_output"} 1.0' in metrics


async def test_upstream_failure_is_a_502_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(_app(httpx.MockTransport(handler))) as client:
        response = await client.post("/score", json=_body(), headers=HEADERS)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


async def test_ready_reflects_llm_reachability() -> None:
    async with _client(_app(_llm_transport([]))) as client:
        assert (await client.get("/ready")).status_code == 200

    down = httpx.MockTransport(lambda request: httpx.Response(503))
    async with _client(_app(down)) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"llm": False}


async def test_ready_is_false_when_llm_url_is_not_configured() -> None:
    settings = Settings(
        internal_token=SecretStr(TOKEN), rubrics_dir=ROOT / "rubrics", prompts_dir=ROOT / "prompts"
    )
    async with _client(build_app(settings)) as client:
        assert (await client.get("/ready")).status_code == 503


async def test_logs_never_contain_the_token_or_evidence(capsys: pytest.CaptureFixture[str]) -> None:
    async with _client(_app(_llm_transport(["garbage", "garbage"]))) as client:
        await client.post("/score", json=_body(), headers=HEADERS)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert "notes export is slow" not in out.out + out.err


async def test_score_without_llm_url_is_a_502_envelope() -> None:
    settings = Settings(
        internal_token=SecretStr(TOKEN), rubrics_dir=ROOT / "rubrics", prompts_dir=ROOT / "prompts"
    )
    async with _client(build_app(settings)) as client:
        response = await client.post("/score", json=_body(), headers=HEADERS)
    assert response.status_code == 502
