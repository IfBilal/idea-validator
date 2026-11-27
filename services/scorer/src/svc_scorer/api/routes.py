"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.errors import UpstreamError
from common.schemas import DimensionScore, ScoreRequest
from common.service import require_internal_token

from ..config import Settings
from ..services.scoring import ScoringService


def scoring_service(request: Request) -> ScoringService:
    service: ScoringService | None = request.app.state.scoring
    if service is None:
        raise UpstreamError("LLM_URL is not configured")
    return service


Scoring = Annotated[ScoringService, Depends(scoring_service)]


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/score", response_model=DimensionScore)
    async def score(body: ScoreRequest, scoring: Scoring) -> DimensionScore:
        return await scoring.score(body)

    return router
