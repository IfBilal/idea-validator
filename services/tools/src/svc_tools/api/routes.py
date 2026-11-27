"""HTTP layer only: validate, call the fetch service, return the result. Internal endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.errors import Unavailable
from common.schemas import FetchRequest, FetchResponse
from common.service import require_internal_token

from ..config import Settings
from ..services.fetch import FetchService
from ..services.llm import LlmRequest, LlmResponse, LlmService


def fetch_service(request: Request) -> FetchService:
    service: FetchService = request.app.state.fetch
    return service


def llm_service(request: Request) -> LlmService:
    service: LlmService | None = request.app.state.llm
    if service is None:
        raise Unavailable("the /llm endpoint is not configured on this instance")
    return service


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post(
        "/fetch",
        response_model=FetchResponse,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    async def fetch(
        body: FetchRequest, service: Annotated[FetchService, Depends(fetch_service)]
    ) -> FetchResponse:
        return await service.fetch(body)

    @router.post("/llm/v1/chat/completions", response_model=LlmResponse)
    async def chat_completions(
        body: LlmRequest, service: Annotated[LlmService, Depends(llm_service)]
    ) -> LlmResponse:
        return await service.complete(body)

    return router
