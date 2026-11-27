"""HTTP layer only: parse, validate, call the service, map errors. Every endpoint is internal."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

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


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

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

    @router.post("/ingest", response_model=IngestResponse)
    async def ingest(body: IngestRequest, kb: Kb) -> IngestResponse:
        return await kb.ingest(list(body.cards))

    @router.post("/reindex", response_model=ReindexResponse)
    async def reindex(kb: Kb) -> ReindexResponse:
        return await kb.reindex()

    return router
