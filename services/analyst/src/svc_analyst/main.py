import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from common.errors import DomainError
from common.limits import BodyLimitMiddleware
from common.logging import get_logger
from common.service import create_app

from .api.routes import make_router
from .clients.fakes import InMemoryCardSource, InMemoryEvidenceSearch
from .clients.kb import KnowledgeBaseClient, UnconfiguredKnowledgeBase
from .config import Settings
from .fixtures import FIXTURE_AS_OF, build_cards
from .models.cards import Card
from .models.ports import CardSource, EvidenceSearch, NicheStore
from .repositories.memory import InMemoryNicheStore
from .services.analyst import AnalystConfig, AnalystService

log = get_logger("analyst")


def build_app(
    settings: Settings,
    *,
    source: CardSource | None = None,
    search: EvidenceSearch | None = None,
    store: NicheStore | None = None,
    kb_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Wire M6 (HTTP client, demo fixtures, or injected fakes), the store and the service."""
    client: KnowledgeBaseClient | None = None
    if source is None:
        if settings.demo_data:
            demo: Sequence[Card] = build_cards()
            source = InMemoryCardSource(demo, snapshot_date=FIXTURE_AS_OF)
            search = InMemoryEvidenceSearch(demo)
        elif settings.kb_url:
            token = settings.internal_token.get_secret_value() if settings.internal_token else None
            client = KnowledgeBaseClient(
                settings.kb_url,
                timeout_s=settings.kb_timeout_s,
                max_retries=settings.kb_max_retries,
                internal_token=token,
                page_size=settings.kb_page_size,
                transport=kb_transport,
            )
            source, search = client, client
        else:
            source = UnconfiguredKnowledgeBase()

    service = AnalystService(
        source,
        store or InMemoryNicheStore(),
        search=search,
        config=AnalystConfig(
            search_k=settings.search_k, as_of=settings.as_of, relevance_tau=settings.relevance_tau
        ),
    )

    async def analysis_ready() -> bool:
        try:
            await service.ensure()
        except DomainError:
            return False
        return True

    async def initial_recompute() -> None:
        try:
            await service.ensure()
        except DomainError:
            # /ready stays false and every request retries; the pod must not crash-loop on M6
            log.warning("initial_recompute_failed", exc_info=True)

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        # a background task: the server accepts probes at once while the recompute runs
        task = asyncio.create_task(initial_recompute()) if settings.recompute_on_start else None
        app_.state.startup_task = task
        try:
            yield
        finally:
            if task is not None and not task.done():
                task.cancel()
            if client is not None:
                await client.aclose()

    app = create_app(
        name="analyst",
        module="M7",
        settings=settings,
        ready_checks={"analysis": analysis_ready},
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.state.analyst = service
    app.state.startup_task = None
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
