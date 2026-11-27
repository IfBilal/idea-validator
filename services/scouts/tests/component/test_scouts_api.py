import asyncio
from typing import Any

import httpx
from pydantic import SecretStr

from svc_scouts.config import Settings
from svc_scouts.main import build_app
from svc_scouts.models.runs import Page
from svc_scouts.models.sources import ScoutSources, SourceCfg
from svc_scouts.testing import FakeFetcher, MemoryStore, fixture

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date?tags=show_hn"
HEADERS = {"X-Internal-Token": "secret"}
SOURCES = ScoutSources(
    sources={
        "hackernews": SourceCfg.model_validate(
            {"enabled": True, "algolia_tags": ["show_hn"], "feeds": []}
        )
    }
)


def app_with(fetcher: FakeFetcher | None = None, **settings: Any) -> Any:
    fetcher = fetcher or FakeFetcher({ALGOLIA: Page(status=200, json_=fixture("hn_algolia_show"))})
    return build_app(
        Settings(internal_token=SecretStr("secret"), **settings),
        store=MemoryStore(),
        fetcher=fetcher,
        sources=SOURCES,
    )


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_scout_requires_the_internal_token() -> None:
    async with client(app_with()) as c:
        assert (await c.post("/scout", json={"source": "hackernews"})).status_code == 401
        bad = await c.post(
            "/scout", json={"source": "hackernews"}, headers={"X-Internal-Token": "x"}
        )
        assert bad.status_code == 401


async def test_scout_runs_one_source_and_returns_the_report() -> None:
    async with client(app_with()) as c:
        response = await c.post("/scout", json={"source": "hn", "limit": 5}, headers=HEADERS)
    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "hackernews" and body["status"] == "ok" and body["items"] == 2


async def test_limits_are_bounded() -> None:
    async with client(app_with(scout_max_limit=10)) as c:
        too_big = await c.post("/scout", json={"source": "hn", "limit": 11}, headers=HEADERS)
        zero = await c.post("/scout", json={"source": "hn", "limit": 0}, headers=HEADERS)
        edge = await c.post("/scout", json={"source": "hn", "limit": 10}, headers=HEADERS)
    assert too_big.status_code == 422 and zero.status_code == 422 and edge.status_code == 200


async def test_malformed_requests_are_rejected_with_the_error_envelope() -> None:
    async with client(app_with()) as c:
        for body in (
            {},
            {"source": ""},
            {"source": "hn", "extra": 1},
            {"source": "hn", "run_id": "a b"},
        ):
            response = await c.post("/scout", json=body, headers=HEADERS)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        raw = await c.post(
            "/scout", content=b"{not json", headers={**HEADERS, "content-type": "application/json"}
        )
        assert raw.status_code == 422


async def test_an_unknown_source_is_404() -> None:
    async with client(app_with()) as c:
        response = await c.post("/scout", json={"source": "myspace"}, headers=HEADERS)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


async def test_a_second_concurrent_run_of_the_same_source_is_409() -> None:
    gate = asyncio.Event()

    class Slow(FakeFetcher):
        async def fetch(self, request, *, source, run_id):  # type: ignore[no-untyped-def]
            await gate.wait()
            return await super().fetch(request, source=source, run_id=run_id)

    slow = Slow({ALGOLIA: Page(status=200, json_=fixture("hn_algolia_show"))})
    async with client(app_with(slow)) as c:
        first = asyncio.create_task(c.post("/scout", json={"source": "hn"}, headers=HEADERS))
        await asyncio.sleep(0.05)
        second = await c.post("/scout", json={"source": "hn"}, headers=HEADERS)
        gate.set()
        assert (await first).status_code == 200
    assert second.status_code == 409 and second.json()["error"]["code"] == "CONFLICT"


async def test_a_run_longer_than_the_timeout_is_a_504_and_frees_the_source() -> None:
    class Hang(FakeFetcher):
        async def fetch(self, request, *, source, run_id):  # type: ignore[no-untyped-def]
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    async with client(app_with(Hang(), scout_timeout_s=0.05)) as c:
        response = await c.post("/scout", json={"source": "hn"}, headers=HEADERS)
        again = await c.post("/scout", json={"source": "hn"}, headers=HEADERS)
    assert response.status_code == 504 and again.status_code == 504  # not 409: the flag was freed


async def test_an_unconfigured_service_answers_503() -> None:
    app = build_app(Settings(internal_token=SecretStr("secret")))
    async with client(app) as c:
        response = await c.post("/scout", json={"source": "hn"}, headers=HEADERS)
    assert response.status_code == 503


async def test_oversized_bodies_are_refused() -> None:
    async with client(app_with()) as c:
        response = await c.post(
            "/scout",
            content=b'{"source": "' + b"a" * 10_000 + b'"}',
            headers={**HEADERS, "content-type": "application/json"},
        )
    assert response.status_code == 413


async def test_a_disabled_source_is_reported_not_run() -> None:
    sources = ScoutSources(sources={"hackernews": SourceCfg(enabled=False)})
    app = build_app(
        Settings(internal_token=SecretStr("secret")),
        store=MemoryStore(),
        fetcher=FakeFetcher(),
        sources=sources,
    )
    async with client(app) as c:
        response = await c.post("/scout", json={"source": "hn"}, headers=HEADERS)
    assert response.json()["status"] == "disabled"


async def test_the_scout_metrics_are_served() -> None:
    app = app_with()
    async with client(app) as c:
        await c.post("/scout", json={"source": "hn"}, headers=HEADERS)
        text = (await c.get("/metrics")).text
    assert 'scout_runs_total{source="hackernews",status="ok"} 1.0' in text
