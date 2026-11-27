from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from common.config import ConfigError
from common.service import create_app

from .api.routes import make_router
from .config import Settings
from .repositories.db import make_engine, make_sessions
from .services.auth import AuthService
from .services.profiles import ProfileService


def build_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.database_url is None:
            raise ConfigError("DATABASE_URL is not set")
        engine = make_engine(settings.database_url.get_secret_value())
        sessions = make_sessions(engine)
        app.state.engine = engine
        app.state.auth = AuthService(sessions, settings)
        app.state.profiles = ProfileService(sessions)
        try:
            yield
        finally:
            await engine.dispose()

    async def database_up() -> bool:
        engine = getattr(app.state, "engine", None)
        if engine is None:
            return False
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    app = create_app(
        name="profile",
        module="M2",
        settings=settings,
        ready_checks={"postgres": database_up},
        lifespan=lifespan,
    )
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
