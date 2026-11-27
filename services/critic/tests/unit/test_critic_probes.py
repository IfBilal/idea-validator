import httpx
import pytest

from svc_critic.clients.knowledge_base import UnconfiguredKnowledgeBase
from svc_critic.config import Settings
from svc_critic.main import app, build_app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_identifies_the_service() -> None:
    async with _client() as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["service"] == "critic"
    assert body["module"] == "M12"


async def test_ready_is_503_while_the_knowledge_base_is_not_configured() -> None:
    async with _client() as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["knowledge_base"] is False


async def test_metrics_are_exposed() -> None:
    async with _client() as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


async def test_unconfigured_knowledge_base_behaviour() -> None:
    kb = UnconfiguredKnowledgeBase()
    assert await kb.is_ready() is False
    await kb.aclose()


async def test_lifespan_closes_the_kb_client() -> None:
    closed: list[bool] = []

    class Spy(UnconfiguredKnowledgeBase):
        async def aclose(self) -> None:
            closed.append(True)

    built = build_app(Settings(), kb=Spy())
    async with built.router.lifespan_context(built):
        pass
    assert closed == [True]


def test_kb_url_set_builds_a_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_URL", "http://kb.example:8000")
    monkeypatch.setenv("INTERNAL_TOKEN", "t")
    built = build_app(Settings())
    assert built.state.checker is not None
