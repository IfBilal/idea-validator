import httpx

from svc_orchestrator.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_identifies_the_service() -> None:
    async with _client() as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["service"] == "orchestrator"
    assert body["module"] == "M3"


async def test_ready_is_503_until_redis_is_reachable() -> None:
    async with _client() as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"redis": False}


async def test_metrics_are_exposed() -> None:
    async with _client() as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
