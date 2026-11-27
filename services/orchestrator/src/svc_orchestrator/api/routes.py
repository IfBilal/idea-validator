"""HTTP layer only: parse, validate, call the run service, map errors. Internal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.service import require_internal_token

from ..config import Settings
from ..models.api import Accepted, AnswerBody, ApproveBody, RunStarted, RunView, StartRun
from ..services.runs import RunService


def run_service(request: Request) -> RunService:
    service: RunService = request.app.state.runs
    return service


Runs = Annotated[RunService, Depends(run_service)]


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/runs", status_code=202, response_model=RunStarted)
    async def start_run(body: StartRun, runs: Runs) -> RunStarted:
        return RunStarted(run_id=await runs.start(body))

    @router.get("/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str, runs: Runs) -> RunView:
        return await runs.get(run_id)

    @router.post("/runs/{run_id}/answer", status_code=202, response_model=Accepted)
    async def answer(run_id: str, body: AnswerBody, runs: Runs) -> Accepted:
        await runs.answer(run_id, body.question_id, body.answer)
        return Accepted(run_id=run_id, state="running")

    @router.post("/runs/{run_id}/approve", status_code=202, response_model=Accepted)
    async def approve(run_id: str, body: ApproveBody, runs: Runs) -> Accepted:
        await runs.approve(run_id, body.approval_id, body.decision)
        return Accepted(run_id=run_id, state="running")

    return router
