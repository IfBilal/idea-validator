"""HTTP layer: auth, validation envelope, wiring of the five endpoints, readiness."""

from typing import Any

import httpx
import pytest
from ideation_fakes import (
    PROMPTS_DIR,
    FakeKb,
    FakeLlm,
    eid,
    evidence,
    evidence_json,
    profile,
    raw_idea,
)
from pydantic import SecretStr

from svc_ideation.config import Settings
from svc_ideation.main import Transports, build_app

TOKEN = "unit-test-token"
HEADERS = {"X-Internal-Token": TOKEN}
E1 = evidence(1, 1, "students lose track of exam dates")


def make_client(llm: FakeLlm, kb: FakeKb | None = None, **cfg: Any) -> httpx.AsyncClient:
    kb = kb or FakeKb(known=[E1], search=lambda _b: [E1])
    settings = Settings(
        internal_token=SecretStr(TOKEN),
        tools_url="http://m5",
        kb_url="http://m6",
        prompts_dir=PROMPTS_DIR,
        max_retries=0,
        **cfg,
    )
    app = build_app(
        settings,
        transports=Transports(
            llm=httpx.MockTransport(llm.handler), kb=httpx.MockTransport(kb.handler)
        ),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def ideate_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": "run_1",
        "profile": profile().model_dump(mode="json"),
        "niches": [],
        "gaps": [
            {
                "gap_id": "gap_1",
                "text": "nothing plans around exams",
                "evidence_ids": [eid(1)],
                "evidence": [evidence_json(E1)],
            }
        ],
        "round": 1,
        "previous_objections": [],
    }
    body.update(over)
    return body


ENDPOINTS = ["/ideate", "/gap-analyst", "/skeptic", "/suggest-changes", "/rewrite"]


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_endpoints_require_the_internal_token(path: str) -> None:
    async with make_client(FakeLlm("{}")) as client:
        missing = await client.post(path, json={})
        wrong = await client.post(path, json={}, headers={"X-Internal-Token": "nope"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_invalid_body_returns_the_standard_422_envelope(path: str) -> None:
    async with make_client(FakeLlm("{}")) as client:
        response = await client.post(path, json={"unknown": 1}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_ideate_happy_path_through_http() -> None:
    llm = FakeLlm({"ideas": [raw_idea()]})
    async with make_client(llm) as client:
        response = await client.post("/ideate", json=ideate_body(), headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["ideas"][0]["title"] == "Study Planner Lite"
    assert body["round"] == 1


@pytest.mark.parametrize("round_", [0, 4])
async def test_ideate_rejects_rounds_outside_one_to_three(round_: int) -> None:
    async with make_client(FakeLlm("{}")) as client:
        response = await client.post("/ideate", json=ideate_body(round=round_), headers=HEADERS)
    assert response.status_code == 422


async def test_ideate_with_garbage_model_output_is_200_with_a_reason_not_500() -> None:
    async with make_client(FakeLlm("garbage")) as client:
        response = await client.post("/ideate", json=ideate_body(), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["ideas"] == []
    assert response.json()["reason"] == "invalid_model_output"


async def test_llm_outage_maps_to_502_envelope() -> None:
    async with make_client(FakeLlm(502)) as client:
        response = await client.post("/ideate", json=ideate_body(), headers=HEADERS)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


async def test_budget_exceeded_maps_to_402() -> None:
    async with make_client(FakeLlm(402)) as client:
        response = await client.post("/ideate", json=ideate_body(), headers=HEADERS)
    assert response.status_code == 402


async def test_gap_analyst_through_http() -> None:
    from svc_ideation.models.ideas import Idea, RawIdea

    idea = Idea.from_raw(RawIdea.model_validate(raw_idea()), "edtech")
    reply = {
        "ideas": [
            {
                "idea_ref": idea.idea_ref,
                "complaints": [{"text": "slow", "evidence_ids": [eid(1)]}],
                "missing_features": [],
            }
        ]
    }
    async with make_client(FakeLlm(reply)) as client:
        response = await client.post(
            "/gap-analyst",
            json={"run_id": "run_1", "ideas": [idea.model_dump(mode="json")]},
            headers=HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["ideas"][0]["complaints"][0]["evidence_ids"] == [eid(1)]


async def test_skeptic_through_http() -> None:
    from svc_ideation.models.ideas import Idea, RawIdea

    idea = Idea.from_raw(RawIdea.model_validate(raw_idea()))
    reply = {
        "objections": [
            {
                "idea_ref": idea.idea_ref,
                "dimension": "competition",
                "claim": "crowded",
                "severity": "major",
                "evidence_ids": [eid(1)],
            }
        ]
    }
    async with make_client(FakeLlm(reply)) as client:
        response = await client.post(
            "/skeptic",
            json={"run_id": "run_1", "ideas": [idea.model_dump(mode="json")]},
            headers=HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["objections"][0]["dimension"] == "competition"


async def test_suggest_changes_through_http() -> None:
    reply = {"changes": [{"text": "narrow it", "rationale": "r", "evidence_ids": [eid(1)]}]}
    body = {
        "run_id": "run_1",
        "idea_text": "a planner",
        "target_dimension": "competition",
        "current_score": 2.0,
        "required_delta": 1.0,
    }
    async with make_client(FakeLlm(reply)) as client:
        response = await client.post("/suggest-changes", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["changes"][0]["target_dimension"] == "competition"


async def test_rewrite_through_http_and_rejection_is_502() -> None:
    body = {"idea_text": "a planner", "change_text": "narrow it"}
    async with make_client(FakeLlm({"new_text": "a narrow planner", "change_note": "n"})) as client:
        ok = await client.post("/rewrite", json=body, headers=HEADERS)
    async with make_client(FakeLlm({"new_text": "a planner"})) as client:
        bad = await client.post("/rewrite", json=body, headers=HEADERS)
    assert ok.json()["new_text"] == "a narrow planner"
    assert bad.status_code == 502
    assert bad.json()["error"]["code"] == "UPSTREAM_ERROR"


async def test_responses_never_contain_secrets_or_verdict_fields() -> None:
    llm = FakeLlm({"ideas": [raw_idea(verdict="GO", score=9)]})
    async with make_client(llm) as client:
        response = await client.post("/ideate", json=ideate_body(), headers=HEADERS)
    assert TOKEN not in response.text
    assert "verdict" not in response.text


async def test_ready_reports_unconfigured_dependencies() -> None:
    app = build_app(Settings(prompts_dir=PROMPTS_DIR))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["prompts"] is True
    assert response.json()["checks"]["tools"] is False


async def test_ready_with_a_missing_prompt_directory_is_not_ready(tmp_path: Any) -> None:
    app = build_app(Settings(prompts_dir=tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.json()["checks"]["prompts"] is False


async def test_unconfigured_service_returns_502_not_500_on_a_call() -> None:
    app = build_app(Settings(internal_token=SecretStr(TOKEN), prompts_dir=PROMPTS_DIR))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/ideate", json=ideate_body(), headers=HEADERS)
    assert response.status_code == 502
