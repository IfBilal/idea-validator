"""HTTP behaviour of M7: auth, schemas, limits, error envelope, readiness, determinism."""

from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from svc_analyst.clients.fakes import InMemoryCardSource, InMemoryEvidenceSearch
from svc_analyst.config import Settings
from svc_analyst.fixtures import build_cards
from svc_analyst.main import build_app

TOKEN = "test-internal-token"
HEADERS = {"X-Internal-Token": TOKEN}
CARDS = build_cards()
PROFILE: dict[str, Any] = {
    "user_id": "u1",
    "skills": ["python", "react"],
    "hours_per_week": 10,
    "weeks_available": 8,
    "team_size": 2,
    "compute": "laptop",
    "goal": "portfolio",
    "interests": ["invoicing"],
}
FEATURE_NAMES = {
    "n_sources",
    "n_platforms",
    "complaint_count",
    "trend_growth",
    "recency_share",
    "n_competitors",
    "competitor_strength",
    "niche_saturation",
    "max_similarity",
    "unaddressed_gap_count",
    "skill_overlap",
    "available_hours",
    "estimated_effort_hours",
    "compute_fit",
    "risk_flags",
    "platform_dependency",
    "skill_novelty",
    "cv_signal",
    "required_skills",
}


def make_app(**over: Any) -> FastAPI:
    settings = Settings(internal_token=SecretStr(TOKEN), **over)
    return build_app(
        settings, source=InMemoryCardSource(CARDS), search=InMemoryEvidenceSearch(CARDS)
    )


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def evidence() -> list[str]:
    return [c.evidence_id for c in CARDS if "invoicing" in c.niche_tags and c.is_complaint][:5]


def features_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "idea_text": "An invoice reminder tool for freelance designers",
        "evidence_ids": evidence(),
        "profile": PROFILE,
    }
    body.update(over)
    return body


# ---- auth ------------------------------------------------------------------------------------


async def test_every_endpoint_requires_the_internal_token() -> None:
    calls = [
        ("GET", "/niches", None),
        ("GET", "/niches/niche_x", None),
        ("POST", "/niches/match", {"profile": PROFILE}),
        ("GET", "/gaps", None),
        ("POST", "/features", features_body()),
        ("POST", "/recompute", None),
    ]
    async with client(make_app()) as c:
        for method, path, body in calls:
            missing = await c.request(method, path, json=body)
            wrong = await c.request(method, path, json=body, headers={"X-Internal-Token": "no"})
            assert missing.status_code == 401, path
            assert wrong.status_code == 401, path
            assert missing.json()["error"]["code"] == "UNAUTHORIZED"


async def test_probes_do_not_need_the_token() -> None:
    async with client(make_app()) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/metrics")).status_code == 200


# ---- niches / gaps ---------------------------------------------------------------------------


async def test_list_niches_and_filter_by_status() -> None:
    async with client(make_app()) as c:
        every = (await c.get("/niches", headers=HEADERS)).json()["niches"]
        rising = (await c.get("/niches", params={"status": "rising"}, headers=HEADERS)).json()
    assert len(every) == 6
    assert {"id", "name", "size", "trend_slope", "status", "keywords"} <= set(every[0])
    assert "series" not in every[0]
    assert rising["niches"] and all(n["status"] == "rising" for n in rising["niches"])


async def test_invalid_status_filter_is_a_validation_error() -> None:
    async with client(make_app()) as c:
        response = await c.get("/niches", params={"status": "booming"}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_niche_detail_has_the_monthly_series() -> None:
    async with client(make_app()) as c:
        first = (await c.get("/niches", headers=HEADERS)).json()["niches"][0]
        detail = (await c.get(f"/niches/{first['id']}", headers=HEADERS)).json()
    assert detail["id"] == first["id"]
    assert detail["series"] and set(detail["series"][0]) == {"month", "launches", "mentions"}
    assert detail["card_ids"]


async def test_unknown_niche_is_a_404_envelope() -> None:
    async with client(make_app()) as c:
        response = await c.get("/niches/niche_00000000", headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_gaps_all_and_by_niche_always_carry_evidence() -> None:
    async with client(make_app()) as c:
        niches = (await c.get("/niches", headers=HEADERS)).json()["niches"]
        every = (await c.get("/gaps", headers=HEADERS)).json()["gaps"]
        mine = (await c.get("/gaps", params={"niche": niches[0]["id"]}, headers=HEADERS)).json()
        none = (await c.get("/gaps", params={"niche": "niche_00000000"}, headers=HEADERS)).json()
    assert every and all(g["evidence_ids"] for g in every)
    assert {
        "id",
        "niche_id",
        "description",
        "evidence_ids",
        "complaint_count",
        "product_coverage",
        "score",
    } == set(every[0])
    assert mine["gaps"] and all(g["niche_id"] == niches[0]["id"] for g in mine["gaps"])
    assert none == {"gaps": []}


# ---- match -----------------------------------------------------------------------------------


async def test_match_returns_ranked_niches() -> None:
    body = {"profile": {**PROFILE, "interests": []}, "niche_wish": "invoice billing", "limit": 3}
    async with client(make_app()) as c:
        response = await c.post("/niches/match", json=body, headers=HEADERS)
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert len(matches) == 3
    assert "invoice" in matches[0]["name"]
    assert matches[0]["score"] >= matches[1]["score"]


async def test_match_validation() -> None:
    async with client(make_app()) as c:
        for bad in (
            {"profile": PROFILE, "limit": 0},
            {"profile": PROFILE, "limit": 21},
            {"profile": PROFILE, "niche_wish": "x" * 501},
            {"profile": PROFILE, "surprise": 1},
            {},
        ):
            response = await c.post("/niches/match", json=bad, headers=HEADERS)
            assert response.status_code == 422, bad
        ok = await c.post(
            "/niches/match", json={"profile": PROFILE, "niche_wish": "x" * 500}, headers=HEADERS
        )
    assert ok.status_code == 200


# ---- features --------------------------------------------------------------------------------


async def test_features_returns_the_flat_dict_of_exact_names() -> None:
    async with client(make_app()) as c:
        response = await c.post("/features", json=features_body(), headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == FEATURE_NAMES
    assert body["n_sources"] == 5
    assert body["available_hours"] == 160
    assert isinstance(body["risk_flags"], list)
    assert isinstance(body["compute_fit"], bool)


async def test_features_are_identical_across_calls() -> None:
    async with client(make_app()) as c:
        a = await c.post("/features", json=features_body(), headers=HEADERS)
        b = await c.post("/features", json=features_body(), headers=HEADERS)
    assert a.content == b.content


async def test_idea_text_length_boundary_is_4000() -> None:
    async with client(make_app()) as c:
        ok = await c.post("/features", json=features_body(idea_text="a" * 4000), headers=HEADERS)
        too_long = await c.post(
            "/features", json=features_body(idea_text="a" * 4001), headers=HEADERS
        )
    assert ok.status_code == 200
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_evidence_id_count_boundary_is_30() -> None:
    ids = [f"card_{i:08x}#c0" for i in range(31)]
    async with client(make_app()) as c:
        ok = await c.post("/features", json=features_body(evidence_ids=ids[:30]), headers=HEADERS)
        too_many = await c.post("/features", json=features_body(evidence_ids=ids), headers=HEADERS)
    assert ok.status_code == 200
    assert too_many.status_code == 422


async def test_features_input_validation() -> None:
    bad_bodies = [
        features_body(idea_text=""),
        features_body(idea_text="   "),
        features_body(evidence_ids=["not-an-id"]),
        features_body(scope="XL"),
        features_body(unknown_field=1),
        features_body(profile={**PROFILE, "hours_per_week": 81}),
        features_body(profile={**PROFILE, "compute": "tpu"}),
        {k: v for k, v in features_body().items() if k != "profile"},
        {k: v for k, v in features_body().items() if k != "idea_text"},
    ]
    async with client(make_app()) as c:
        for body in bad_bodies:
            response = await c.post("/features", json=body, headers=HEADERS)
            assert response.status_code == 422, body
        invalid_json = await c.post(
            "/features", content=b"{nope", headers={**HEADERS, "content-type": "application/json"}
        )
    assert invalid_json.status_code == 422


async def test_features_with_empty_evidence_is_allowed() -> None:
    async with client(make_app()) as c:
        response = await c.post("/features", json=features_body(evidence_ids=[]), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["n_sources"] == 0


async def test_oversized_body_is_rejected_with_413() -> None:
    async with client(make_app(max_body_bytes=2048)) as c:
        response = await c.post(
            "/features", json=features_body(idea_text="a" * 3000), headers=HEADERS
        )
    assert response.status_code == 413


# ---- recompute / readiness -------------------------------------------------------------------


async def test_recompute_returns_counts() -> None:
    async with client(make_app()) as c:
        response = await c.post("/recompute", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["cards"] == len(CARDS)
    assert body["niches"] == 6
    assert body["gaps"] >= 6
    assert body["unassigned"] == 0
    assert body["as_of"]


async def test_ready_when_analysis_is_available() -> None:
    async with client(make_app()) as c:
        response = await c.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["analysis"] is True


async def test_unconfigured_knowledge_base_is_not_ready_and_endpoints_say_unavailable() -> None:
    app = build_app(Settings(internal_token=SecretStr(TOKEN)))
    async with client(app) as c:
        ready = await c.get("/ready")
        niches = await c.get("/niches", headers=HEADERS)
    assert ready.status_code == 503
    assert niches.status_code == 503
    assert niches.json()["error"]["code"] == "UPSTREAM_ERROR"


async def test_demo_data_flag_runs_without_m6() -> None:
    app = build_app(Settings(internal_token=SecretStr(TOKEN), demo_data=True))
    async with client(app) as c:
        assert (await c.get("/ready")).status_code == 200
        assert len((await c.get("/niches", headers=HEADERS)).json()["niches"]) == 6


async def test_kb_over_http_end_to_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/cards":
            page = [c.model_dump(mode="json") for c in CARDS]
            return httpx.Response(200, json={"items": page, "total": len(page)})
        return httpx.Response(200, json={"items": []})

    app = build_app(
        Settings(internal_token=SecretStr(TOKEN), kb_url="http://kb.test", kb_max_retries=0),
        kb_transport=httpx.MockTransport(handler),
    )
    async with client(app) as c:
        assert (await c.get("/ready")).status_code == 200
        niches = (await c.get("/niches", headers=HEADERS)).json()["niches"]
        features = await c.post("/features", json=features_body(), headers=HEADERS)
    assert len(niches) == 6
    assert features.status_code == 200


async def test_kb_failure_maps_to_an_upstream_error_envelope() -> None:
    app = build_app(
        Settings(internal_token=SecretStr(TOKEN), kb_url="http://kb.test", kb_max_retries=0),
        kb_transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    async with client(app) as c:
        response = await c.get("/niches", headers=HEADERS)
        ready = await c.get("/ready")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"
    assert ready.status_code == 503


async def test_token_never_appears_in_logs(capsys: Any) -> None:
    async with client(make_app()) as c:
        await c.post("/features", json=features_body(), headers=HEADERS)
        await c.get("/niches", headers={"X-Internal-Token": "wrong-secret-value"})
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "wrong-secret-value" not in captured.out + captured.err


async def test_startup_recomputes_so_the_first_request_is_instant() -> None:
    app = make_app()
    service = app.state.analyst
    assert service.ready is False
    async with app.router.lifespan_context(app):
        assert service.ready is True


async def test_startup_survives_a_dead_knowledge_base_and_closes_the_client() -> None:
    app = build_app(
        Settings(internal_token=SecretStr(TOKEN), kb_url="http://kb.test", kb_max_retries=0),
        kb_transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    async with app.router.lifespan_context(app):
        assert app.state.analyst.ready is False


async def test_startup_recompute_can_be_disabled() -> None:
    app = make_app(recompute_on_start=False)
    async with app.router.lifespan_context(app):
        assert app.state.analyst.ready is False
