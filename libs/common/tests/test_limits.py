from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request

from common.limits import BodyLimitMiddleware

LIMIT = 100


def app() -> FastAPI:
    api = FastAPI()

    @api.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"n": len(await request.body())}

    api.add_middleware(BodyLimitMiddleware, max_bytes=LIMIT)
    return api


async def post(
    content: bytes | AsyncIterator[bytes], headers: dict[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://t"
    ) as c:
        return await c.post("/echo", content=content, headers=headers)


@pytest.mark.parametrize(
    ("size", "status"), [(0, 200), (LIMIT - 1, 200), (LIMIT, 200), (LIMIT + 1, 413)]
)
async def test_the_boundary_is_exact(size: int, status: int) -> None:
    assert (await post(b"x" * size)).status_code == status


async def test_chunked_bodies_over_the_limit_are_refused() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 40

    assert (await post(chunks())).status_code == 413


async def test_a_lying_or_garbage_content_length_does_not_bypass_the_limit() -> None:
    assert (await post(b"x" * 500, {"Content-Length": "5"})).status_code in {413, 400}
    assert (await post(b"x" * 500, {"Content-Length": "abc"})).status_code in {413, 400}


async def test_the_refusal_uses_the_standard_error_envelope() -> None:
    r = await post(b"x" * 500)
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_a_declared_oversize_body_is_refused_before_it_is_read() -> None:
    r = await post(b"x", {"Content-Length": "999999"})
    assert r.status_code in {413, 400}
