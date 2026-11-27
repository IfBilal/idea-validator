from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from common.config import ConfigError
from common.limits import BodyLimitMiddleware
from common.service import create_app
from svc_scouts.models.sources import ScoutSources, load_scout_sources

from .api.routes import make_router
from .clients.tools import ToolsClient
from .config import Settings
from .repositories.raw_pages import PgRawStore
from .services.context import Credentials
from .services.metrics import ScoutMetrics
from .services.ports import Fetcher, RawStore
from .services.runner import ScoutRunner


def credentials_from(settings: Settings) -> Credentials:
    return Credentials(
        github_token=settings.github_token,
        reddit_client_id=settings.reddit_client_id,
        reddit_client_secret=settings.reddit_client_secret,
        producthunt_token=settings.producthunt_token,
    )


def build_runner(
    settings: Settings,
    *,
    store: RawStore,
    fetcher: Fetcher,
    sources: ScoutSources,
    metrics: ScoutMetrics,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ScoutRunner:
    return ScoutRunner(
        sources=sources,
        store=store,
        fetcher=fetcher,
        credentials=credentials_from(settings),
        metrics=metrics,
        breaker_threshold=settings.breaker_threshold,
        breaker_cooldown_s=settings.breaker_cooldown_s,
        default_limit=settings.default_limit,
        clock=clock,
    )


def tools_client(settings: Settings) -> ToolsClient | None:
    if not settings.tools_url:
        return None
    token = settings.internal_token.get_secret_value() if settings.internal_token else None
    return ToolsClient(
        settings.tools_url,
        internal_token=token,
        timeout_s=settings.fetch_timeout_s,
        max_retries=settings.fetch_retries,
        forward_auth=settings.m5_forward_auth,
    )


def build_app(
    settings: Settings,
    *,
    store: RawStore | None = None,
    fetcher: Fetcher | None = None,
    sources: ScoutSources | None = None,
) -> FastAPI:
    """Wire the service. Without a database, M5 address or sources it starts but is not ready."""
    engine: AsyncEngine | None = None
    tools: ToolsClient | None = None
    if store is None and settings.database_url:
        engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
        store = PgRawStore(async_sessionmaker(engine, expire_on_commit=False))
    if fetcher is None:
        tools = tools_client(settings)
        fetcher = tools
    if sources is None:
        try:
            sources = load_scout_sources(settings.config_dir)
        except ConfigError:
            sources = None

    async def storage_ready() -> bool:
        return store is not None and await store.ping()

    async def config_ready() -> bool:
        return sources is not None and fetcher is not None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if tools is not None:
                await tools.aclose()
            if engine is not None:
                await engine.dispose()

    app = create_app(
        name="scouts",
        module="M4",
        settings=settings,
        ready_checks={"storage": storage_ready, "config": config_ready},
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    metrics = ScoutMetrics(app.state.metrics_registry)
    app.state.runner = (
        build_runner(settings, store=store, fetcher=fetcher, sources=sources, metrics=metrics)
        if store is not None and fetcher is not None and sources is not None
        else None
    )
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
