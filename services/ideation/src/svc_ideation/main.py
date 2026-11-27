from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.service import create_app

from .api.routes import make_router
from .config import Settings
from .runtime import Runtime, Transports


def build_app(settings: Settings, *, transports: Transports | None = None) -> FastAPI:
    runtime = Runtime(settings, transports)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await runtime.aclose()

    app = create_app(
        name="ideation",
        module="M8",
        settings=settings,
        ready_checks={
            "prompts": runtime.prompts_ok,
            "tools": runtime.tools_up,
            "kb": runtime.kb_up,
            "analyst": runtime.analyst_up,
        },
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())

__all__ = ["Transports", "app", "build_app"]
