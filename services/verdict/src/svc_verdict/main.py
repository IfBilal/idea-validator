from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.service import create_app

from .api.routes import make_router
from .config import Settings
from .services.engine_config import load_engine_config


def build_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Invalid or missing rules stop the service from starting (docs/harness/08 §0).
        app.state.engine_config = load_engine_config(settings.config_dir)
        yield

    async def rules_loaded() -> bool:
        return app.state.engine_config is not None

    app = create_app(
        name="verdict",
        module="M11",
        settings=settings,
        ready_checks={"rules": rules_loaded},
        lifespan=lifespan,
    )
    app.state.engine_config = None
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
