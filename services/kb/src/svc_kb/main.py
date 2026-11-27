from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from common.limits import BodyLimitMiddleware
from common.service import create_app

from .api.routes import make_router
from .config import Settings
from .services.kb import KbService, UnconfiguredKb
from .services.models import DENSE_DIM, FastEmbedModels
from .services.search import ChunkIndex
from .services.store import PgStore, Store


def build_app(
    settings: Settings, *, store: Store | None = None, index: ChunkIndex | None = None
) -> FastAPI:
    """Wire the service. Without a database and Qdrant it starts, answers 503, is not ready."""
    owned: list[AsyncQdrantClient] = []
    engine = None
    if store is None and index is None and settings.database_url and settings.qdrant_url:
        engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
        store = PgStore(async_sessionmaker(engine, expire_on_commit=False))
        client = AsyncQdrantClient(url=settings.qdrant_url)
        owned.append(client)
        index = ChunkIndex(
            client,
            FastEmbedModels(settings.model_cache_dir),
            collection=settings.qdrant_collection,
            dim=DENSE_DIM,
        )
    kb: KbService | UnconfiguredKb = (
        KbService(store, index) if store is not None and index is not None else UnconfiguredKb()
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if index is not None and owned:
                await index.ensure_collection()
            yield
        finally:
            for client in owned:
                await client.close()
            if engine is not None:
                await engine.dispose()

    app = create_app(
        name="kb",
        module="M6",
        settings=settings,
        ready_checks={"storage": kb.ready},
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.state.kb = kb
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
