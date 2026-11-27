"""HTTP layer only: validate, call the fetch service, return the result. Internal endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.schemas import FetchRequest, FetchResponse
from common.service import require_internal_token

from ..config import Settings
from ..services.fetch import FetchService


def fetch_service(request: Request) -> FetchService:
    service: FetchService = request.app.state.fetch
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

    return router
