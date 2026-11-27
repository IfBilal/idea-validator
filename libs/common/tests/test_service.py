from collections.abc import Mapping

import httpx
import pytest
from fastapi import FastAPI

from common.config import ServiceSettings
from common.errors import NotFound
from common.service import ReadyCheck, create_app, require_internal_token


def make(ready_checks: Mapping[str, ReadyCheck] | None = None) -> FastAPI:
    app = create_app(name="kb", module="M6", ready_checks=ready_checks)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    @app.get("/missing")
    async def missing() -> None:
        raise NotFound("card not found", {"card_id": "c1"})

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_health_reports_service_identity() -> None:
    async with client(make()) as c:
        body = (await c.get("/health")).json()
    assert body["status"] == "ok"
    assert (body["service"], body["module"]) == ("kb", "M6")


async def test_ready_is_503_until_every_dependency_is_up() -> None:
    state = {"db": False}

    async def db() -> bool:
        return state["db"]

    async def broken() -> bool:
        raise OSError

    async with client(make(ready_checks={"db": db, "redis": broken})) as c:
        down = await c.get("/ready")
        state["db"] = True
        still_down = await c.get("/ready")
    assert down.status_code == 503
    assert down.json()["checks"] == {"db": False, "redis": False}
    assert still_down.json()["checks"] == {"db": True, "redis": False}


async def test_ready_is_200_with_no_dependencies() -> None:
    async with client(make()) as c:
        assert (await c.get("/ready")).status_code == 200


async def test_request_id_is_generated_and_echoed() -> None:
    async with client(make()) as c:
        generated = await c.get("/health")
        echoed = await c.get("/health", headers={"X-Request-Id": "req_abc"})
    assert generated.headers["x-request-id"].startswith("req_")
    assert echoed.headers["x-request-id"] == "req_abc"


async def test_domain_errors_use_the_standard_envelope() -> None:
    async with client(make()) as c:
        r = await c.get("/missing", headers={"X-Request-Id": "req_1"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "card not found",
            "details": {"card_id": "c1"},
            "request_id": "req_1",
        }
    }


async def test_validation_errors_are_422_with_the_envelope() -> None:
    async with client(make()) as c:
        r = await c.get("/items/abc")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_unexpected_errors_never_leak_internals() -> None:
    async with client(make()) as c:
        r = await c.get("/boom", headers={"X-Request-Id": "req_9"})
    assert r.status_code == 500
    assert "secret internals" not in r.text
    assert r.json()["error"]["code"] == "INTERNAL"
    assert r.json()["error"]["request_id"] == "req_9"


async def test_metrics_count_requests_by_route_template() -> None:
    async with client(make()) as c:
        await c.get("/items/1")
        await c.get("/items/2")
        text = (await c.get("/metrics")).text
    assert (
        'http_requests_total{method="GET",path="/items/{item_id}",service="kb",status="200"} 2.0'
        in text
    )


async def test_internal_token_dependency() -> None:
    settings = ServiceSettings(service_name="verdict", internal_token="tok")
    app = create_app(name="verdict", module="M11", settings=settings)
    from fastapi import Depends

    @app.get("/internal/x", dependencies=[Depends(require_internal_token(settings))])
    async def internal() -> dict[str, bool]:
        return {"ok": True}

    async with client(app) as c:
        assert (await c.get("/internal/x")).status_code == 401
        assert (await c.get("/internal/x", headers={"X-Internal-Token": "nope"})).status_code == 401
        assert (await c.get("/internal/x", headers={"X-Internal-Token": "tok"})).status_code == 200


async def test_internal_endpoints_are_closed_when_no_token_is_configured() -> None:
    settings = ServiceSettings(service_name="verdict")
    dependency = require_internal_token(settings)
    from starlette.requests import Request

    from common.errors import Unauthorized

    with pytest.raises(Unauthorized):
        dependency(Request({"type": "http", "headers": []}))


async def test_rate_limited_errors_carry_a_retry_after_header() -> None:
    from common.errors import RateLimited

    app = make()

    @app.get("/slow-down")
    async def slow_down() -> None:
        raise RateLimited("too many requests", {"retry_after": 17})

    async with client(app) as c:
        r = await c.get("/slow-down")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "17"
    assert r.json()["error"]["code"] == "RATE_LIMITED"
