"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.service import require_internal_token

from ..config import Settings
from ..models.api import (
    GapAnalystRequest,
    GapAnalystResponse,
    IdeateRequest,
    IdeateResponse,
    RewriteRequest,
    RewriteResponse,
    SkepticRequest,
    SkepticResponse,
    SuggestChangesRequest,
    SuggestChangesResponse,
)
from ..runtime import Agents


def agents_dependency(request: Request) -> Agents:
    agents: Agents = request.app.state.runtime.agents()
    return agents


Deps = Annotated[Agents, Depends(agents_dependency)]


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/ideate", response_model=IdeateResponse)
    async def ideate(body: IdeateRequest, agents: Deps) -> IdeateResponse:
        return await agents.ideator.ideate(body)

    @router.post("/gap-analyst", response_model=GapAnalystResponse)
    async def gap_analyst(body: GapAnalystRequest, agents: Deps) -> GapAnalystResponse:
        return await agents.gap_analyst.analyse(body)

    @router.post("/skeptic", response_model=SkepticResponse)
    async def skeptic(body: SkepticRequest, agents: Deps) -> SkepticResponse:
        return await agents.skeptic.challenge(body)

    @router.post("/suggest-changes", response_model=SuggestChangesResponse)
    async def suggest_changes(body: SuggestChangesRequest, agents: Deps) -> SuggestChangesResponse:
        return await agents.changes.suggest(body)

    @router.post("/rewrite", response_model=RewriteResponse)
    async def rewrite(body: RewriteRequest, agents: Deps) -> RewriteResponse:
        return await agents.rewriter.rewrite(body)

    return router
