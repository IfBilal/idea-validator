"""HTTP layer only: parse, validate, call the service, map errors. Every endpoint is internal."""

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from common.errors import Forbidden, Unauthorized
from common.service import require_internal_token
from svc_kb.config import Settings
from svc_kb.models.api import (
    CardsBatchResponse,
    CardsPage,
    EvidenceBatchResponse,
    EvidenceFull,
    IdsRequest,
    IngestRequest,
    IngestResponse,
    ReindexResponse,
    SearchRequest,
    SearchResponse,
)
from svc_kb.models.cards import CardOut, KbStats
from svc_kb.services.kb import KbService
from svc_kb.services.store import MAX_PAGE


def kb_service(request: Request) -> KbService:
    service: KbService = request.app.state.kb
    return service


Kb = Annotated[KbService, Depends(kb_service)]


INGEST_TOKEN_HEADER = "X-Ingest-Token"  # noqa: S105 (header name, not a secret)


def require_ingest_token(settings: Settings) -> Callable[[Request], None]:
    """Writes need a second credential that the read-only agent services never get."""

    def dependency(request: Request) -> None:
        if settings.ingest_token is None:
            raise Forbidden("writes are disabled: INGEST_TOKEN is not set")
        given = request.headers.get(INGEST_TOKEN_HEADER, "")
        if not hmac.compare_digest(
            given.encode(), settings.ingest_token.get_secret_value().encode()
        ):
            raise Unauthorized("missing or invalid ingest token")

    return dependency


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])
    writes = [Depends(require_ingest_token(settings))]

    @router.post("/search", response_model=SearchResponse)
    async def search(body: SearchRequest, kb: Kb) -> SearchResponse:
        return await kb.search(body)

    @router.post("/evidence/batch", response_model=EvidenceBatchResponse)
    async def evidence_batch(body: IdsRequest, kb: Kb) -> EvidenceBatchResponse:
        return await kb.evidence_batch(body.ids)

    @router.get("/evidence/{evidence_id}", response_model=EvidenceFull)
    async def evidence_one(evidence_id: str, kb: Kb) -> EvidenceFull:
        return await kb.evidence_one(evidence_id)

    @router.get("/cards", response_model=CardsPage)
    async def cards_page(
        kb: Kb,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> CardsPage:
        return await kb.cards_page(limit=limit, offset=offset)

    @router.post("/cards/batch", response_model=CardsBatchResponse)
    async def cards_batch(body: IdsRequest, kb: Kb) -> CardsBatchResponse:
        return await kb.cards_batch(body.ids)

    @router.get("/cards/{card_id}", response_model=CardOut)
    async def card(card_id: str, kb: Kb) -> CardOut:
        return await kb.card(card_id)

    @router.get("/stats", response_model=KbStats)
    async def stats(kb: Kb) -> KbStats:
        return await kb.stats()

    @router.post("/ingest", response_model=IngestResponse, dependencies=writes)
    async def ingest(body: IngestRequest, kb: Kb) -> IngestResponse:
        return await kb.ingest(list(body.cards))

    @router.post("/reindex", response_model=ReindexResponse, dependencies=writes)
    async def reindex(kb: Kb) -> ReindexResponse:
        return await kb.reindex()

    return router
