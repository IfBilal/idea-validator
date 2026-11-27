"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from common.service import require_internal_token

from ..config import Settings
from ..models.api import (
    FeaturesRequest,
    FeaturesResponse,
    GapList,
    MatchRequest,
    MatchResponse,
    NicheDetail,
    NicheList,
    NicheSummary,
    RecomputeResponse,
)
from ..models.domain import Niche, NicheStatus
from ..services.analyst import AnalystService


def analyst_dependency(request: Request) -> AnalystService:
    service: AnalystService = request.app.state.analyst
    return service


Analyst = Annotated[AnalystService, Depends(analyst_dependency)]


def _summary(niche: Niche) -> NicheSummary:
    return NicheSummary.model_validate(niche.model_dump(include=set(NicheSummary.model_fields)))


def make_router(settings: Settings) -> APIRouter:
    # the body size limit is enforced by common.limits.BodyLimitMiddleware (chunked-safe)
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.get("/niches", response_model=NicheList)
    async def list_niches(
        service: Analyst, status: Annotated[NicheStatus | None, Query()] = None
    ) -> NicheList:
        return NicheList(niches=[_summary(n) for n in await service.list_niches(status)])

    @router.post("/niches/match", response_model=MatchResponse)
    async def match(body: MatchRequest, service: Analyst) -> MatchResponse:
        return MatchResponse(matches=await service.match(body.profile, body.niche_wish, body.limit))

    @router.get("/niches/{niche_id}", response_model=NicheDetail)
    async def get_niche(niche_id: str, service: Analyst) -> NicheDetail:
        niche = await service.get_niche(niche_id)
        return NicheDetail.model_validate(niche.model_dump(include=set(NicheDetail.model_fields)))

    @router.get("/gaps", response_model=GapList)
    async def list_gaps(
        service: Analyst, niche: Annotated[str | None, Query(max_length=64)] = None
    ) -> GapList:
        return GapList(gaps=await service.list_gaps(niche))

    @router.post("/features", response_model=FeaturesResponse)
    async def features(body: FeaturesRequest, service: Analyst) -> FeaturesResponse:
        return await service.features(body)

    @router.post("/recompute", response_model=RecomputeResponse)
    async def recompute(service: Analyst) -> RecomputeResponse:
        return await service.recompute()

    return router
