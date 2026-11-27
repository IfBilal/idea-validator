import httpx

from svc_scouts.config import Settings
from svc_scouts.main import app, build_app
from svc_scouts.models.sources import ScoutSources
from svc_scouts.testing import FakeFetcher, MemoryStore


def _client(target: object = app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=target), base_url="http://test")  # type: ignore[arg-type]


async def test_health_identifies_the_service() -> None:
    async with _client() as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["service"] == "scouts"
    assert body["module"] == "M4"


async def test_not_ready_without_a_database_or_the_tool_layer() -> None:
    async with _client() as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"storage": False, "config": False}


async def test_ready_when_storage_tools_and_sources_are_wired() -> None:
    wired = build_app(
        Settings(), store=MemoryStore(), fetcher=FakeFetcher(), sources=ScoutSources()
    )
    async with _client(wired) as client:
        assert (await client.get("/ready")).status_code == 200


async def test_metrics_are_exposed_including_the_scout_series() -> None:
    wired = build_app(
        Settings(), store=MemoryStore(), fetcher=FakeFetcher(), sources=ScoutSources()
    )
    async with _client(wired) as client:
        text = (await client.get("/metrics")).text
    assert "http_requests_total" in text
    assert "scout_source_up" in text or "scout_runs_total" in text or "# HELP scout_" in text
