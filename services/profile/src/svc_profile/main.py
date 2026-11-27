from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import Counter
from redis.asyncio import Redis
from sqlalchemy import text

from common.config import ConfigError
from common.service import create_app

from .api.routes import make_router
from .config import Settings
from .repositories.db import make_engine, make_sessions
from .services.auth import AuthService
from .services.cache import CacheService
from .services.ideas import IdeaService
from .services.profiles import ProfileService
from .services.runs import ApprovalService, RunService
from .services.verdicts import VerdictService


def build_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.database_url is None:
            raise ConfigError("DATABASE_URL is not set")
        if settings.redis_url is None:
            raise ConfigError("REDIS_URL is not set")
        engine = make_engine(settings.database_url.get_secret_value())
        sessions = make_sessions(engine)
        app.state.engine = engine
        app.state.auth = AuthService(sessions, settings)
        app.state.profiles = ProfileService(sessions)
        app.state.ideas = IdeaService(sessions)
        app.state.verdicts = VerdictService(sessions)
        app.state.runs = RunService(sessions)
        app.state.approvals = ApprovalService(sessions)
        redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
        registry = app.state.metrics_registry
        app.state.redis = redis
        app.state.cache = CacheService(
            redis,
            settings.verdict_cache_ttl_s,
            Counter("cache_hits_total", "Cache hits", ["cache"], registry=registry),
            Counter("cache_misses_total", "Cache misses", ["cache"], registry=registry),
        )
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    async def database_up() -> bool:
        engine = getattr(app.state, "engine", None)
        if engine is None:
            return False
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def redis_up() -> bool:
        cache = getattr(app.state, "cache", None)
        return cache is not None and await cache.ping()

    app = create_app(
        name="profile",
        module="M2",
        settings=settings,
        ready_checks={"postgres": database_up, "redis": redis_up},
        lifespan=lifespan,
    )
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
