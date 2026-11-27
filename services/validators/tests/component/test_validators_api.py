"""HTTP behaviour of M9 with a mocked M10: auth, schemas, error envelope, readiness."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from svc_validators.config import Settings
from svc_validators.main import build_app

ROOT = Path(__file__).resolve().parents[4]
TOKEN = "test-internal-token"
HEADERS = {"X-Internal-Token": TOKEN}
ID1 = "card_8f21a3b4#c2"
DIMENSIONS = ["demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"]

FEATURES: dict[str, Any] = {
    "n_sources": 4,
    "n_platforms": 2,
    "complaint_count": 3,
    "trend_growth": 0.2,
    "recency_share": 0.6,
    "n_competitors": 1,
    "niche_saturation": 0.5,
    "competitor_strength": 0.2,
    "unaddressed_gap_count": 1,
    "max_similarity": 0.7,
    "estimated_effort_hours": 50,
    "available_hours": 100,
    "skill_overlap": 0.9,
    "compute_fit": True,
    "skill_novelty": 2,
    "cv_signal": 0.6,
    "risk_flags": [],
    "platform_dependency": False,
}


def _m10(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/ready":
        return httpx.Response(200, json={"status": "ready"})
    sent = json.loads(request.content)
    rubric_id = sent["rubric_id"]
    return httpx.Response(
        200,
        json={
            "dimension": rubric_id.split("@")[0],
            "score": 3,
            "confidence": 0.9,
            "evidence_ids": [ID1],
            "reason": "from m10",
            "rubric_version": rubric_id,
            "model": "scorer-lora@v1",
        },
    )


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "internal_token": SecretStr(TOKEN),
        "scorer_url": "http://scorer.test",
        "rubrics_dir": ROOT / "rubrics",
        "config_dir": ROOT / "config",
        "scorer_max_retries": 0,
    }
    base.update(over)
    return Settings(**base)


def _app(handler: Any = _m10, **over: Any) -> FastAPI:
    return build_app(_settings(**over), scorer_transport=httpx.MockTransport(handler))


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": "run_1",
        "idea": "A study planner",
        "evidence": [{"evidence_id": ID1, "quote": "export is slow"}],
        "features": FEATURES,
    }
    body.update(over)
    return body


async def test_score_all_requires_the_internal_token() -> None:
    async with _client(_app()) as client:
        assert (await client.post("/score-all", json=_body())).status_code == 401
        wrong = await client.post("/score-all", json=_body(), headers={"X-Internal-Token": "x"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "UNAUTHORIZED"


async def test_score_all_returns_six_scores_and_extras() -> None:
    async with _client(_app()) as client:
        response = await client.post("/score-all", json=_body(), headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert [s["dimension"] for s in body["scores"]] == DIMENSIONS
    assert body["abstained"] == []
    assert body["errors"] == []
    assert set(body["extra"]) == {
        "estimated_effort_hours",
        "required_skills",
        "risk_blocker",
        "duplicate_of",
    }
    assert all("verdict" not in s for s in body["scores"])
    assert body["scores"][0]["model"] == "scorer-lora@v1"


async def test_single_scorer_endpoint() -> None:
    async with _client(_app()) as client:
        response = await client.post("/score/risk", json=_body(), headers=HEADERS)
        missing = await client.post("/score/verdict", json=_body(), headers=HEADERS)
    assert [s["dimension"] for s in response.json()["scores"]] == ["risk"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "bad",
    [
        _body(surprise=1),
        _body(idea="x" * 4001),
        _body(evidence=[{"evidence_id": "bad", "quote": "q"}]),
        _body(features="not a dict"),
        {k: v for k, v in _body().items() if k != "run_id"},
    ],
)
async def test_invalid_requests_get_the_error_envelope(bad: dict[str, Any]) -> None:
    async with _client(_app()) as client:
        response = await client.post("/score-all", json=bad, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_m10_down_yields_abstentions_not_a_failed_request() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _client(_app(down)) as client:
        response = await client.post("/score-all", json=_body(), headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] == DIMENSIONS
    assert len(body["errors"]) == 6  # one note per failed dimension; the request still succeeds


async def test_m10_invalid_payload_is_isolated_to_that_scorer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["rubric_id"].startswith("risk"):
            return httpx.Response(200, json={"nonsense": True})
        return _m10(request)

    async with _client(_app(handler)) as client:
        body = (await client.post("/score-all", json=_body(), headers=HEADERS)).json()
    assert body["abstained"] == ["risk"]


async def test_missing_scorer_url_is_a_502_envelope() -> None:
    app = build_app(_settings(scorer_url=None))
    async with _client(app) as client:
        response = await client.post("/score-all", json=_body(), headers=HEADERS)
    assert response.status_code == 502


async def test_alpha_env_override_changes_the_blend() -> None:
    async with _client(_app(alpha_model=1.0)) as client:
        body = (await client.post("/score-all", json=_body(), headers=HEADERS)).json()
    assert body["scores"][0]["score"] == 3.0  # model only


async def test_ready_checks_scorer_and_config() -> None:
    async with _client(_app()) as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"config": True, "scorer": True}

    down = httpx.MockTransport(lambda request: httpx.Response(503))
    async with _client(build_app(_settings(), scorer_transport=down)) as client:
        not_ready = await client.get("/ready")
    assert not_ready.status_code == 503
    assert not_ready.json()["checks"] == {"config": True, "scorer": False}


async def test_ready_is_false_with_a_broken_rubrics_dir(tmp_path: Path) -> None:
    async with _client(_app(rubrics_dir=tmp_path)) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["config"] is False


async def test_startup_fails_fast_on_a_bad_rubrics_dir(tmp_path: Path) -> None:
    from svc_validators.services.rubrics import RubricError

    app = _app(rubrics_dir=tmp_path)
    with pytest.raises(RubricError):
        async with app.router.lifespan_context(app):
            pass


async def test_lifespan_closes_cleanly_with_valid_config() -> None:
    app = _app()
    async with app.router.lifespan_context(app):
        pass


async def test_logs_never_contain_the_token_or_evidence(capsys: pytest.CaptureFixture[str]) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(_app(down)) as client:
        await client.post("/score-all", json=_body(), headers=HEADERS)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert "export is slow" not in out.out + out.err
