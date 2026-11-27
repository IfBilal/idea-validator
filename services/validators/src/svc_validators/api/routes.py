"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.service import require_internal_token

from ..config import Settings
from ..models.api import ScoreAllRequest, ScoreAllResponse
from ..services.runner import ValidationRunner


def runner_dependency(request: Request) -> ValidationRunner:
    runner: ValidationRunner = request.app.state.runtime.runner()
    return runner


Runner = Annotated[ValidationRunner, Depends(runner_dependency)]


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/score-all", response_model=ScoreAllResponse)
    async def score_all(body: ScoreAllRequest, runner: Runner) -> ScoreAllResponse:
        return await runner.score_all(body)

    @router.post("/score/{scorer}", response_model=ScoreAllResponse)
    async def score_one(scorer: str, body: ScoreAllRequest, runner: Runner) -> ScoreAllResponse:
        return await runner.score_one(scorer, body)

    return router
