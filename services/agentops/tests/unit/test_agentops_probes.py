import httpx

from svc_agentops.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_identifies_the_service() -> None:
    async with _client() as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["service"] == "agentops"
    assert body["module"] == "M13"


async def test_ready_is_503_without_a_database() -> None:
    async with _client() as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] is False


async def test_trace_endpoints_answer_503_when_the_store_is_not_configured() -> None:
    async with _client() as client:
        response = await client.get("/runs", headers={"X-Internal-Token": "x"})
    assert response.status_code in (401, 503)


async def test_metrics_are_exposed() -> None:
    async with _client() as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
