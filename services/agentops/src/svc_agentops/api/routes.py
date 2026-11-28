"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from common.errors import Unavailable, ValidationFailed
from common.service import require_internal_token

from ..config import Settings
from ..models.traces import IngestResult, RunList, TraceFlat, TraceTree
from ..services.ingest import IngestService
from ..services.query import QueryService

USER_ID = r"^[A-Za-z0-9_.:-]{1,128}$"


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    def ingest_service(request: Request) -> IngestService:
        service: IngestService | None = getattr(request.app.state, "ingest", None)
        if service is None:
            raise Unavailable("trace store is not configured (DATABASE_URL)")
        return service

    def query_service(request: Request) -> QueryService:
        service: QueryService | None = getattr(request.app.state, "query", None)
        if service is None:
            raise Unavailable("trace store is not configured (DATABASE_URL)")
        return service

    @router.post("/traces", response_model=IngestResult)
    async def post_traces(
        request: Request, service: Annotated[IngestService, Depends(ingest_service)]
    ) -> IngestResult:
        try:
            payload: Any = await request.json()
        except ValueError as exc:  # also UnicodeDecodeError
            raise ValidationFailed("body must be JSON: one trace record or a list of them") from exc
        if not isinstance(payload, dict | list):
            raise ValidationFailed("body must be a trace record or a list of trace records")
        try:
            return await service.ingest(payload)
        except ValidationError as exc:  # pragma: no cover - records are validated one by one
            raise ValidationFailed("invalid trace record") from exc

    @router.get("/runs/{run_id}/trace", response_model=TraceTree | TraceFlat)
    async def get_trace(
        run_id: str,
        service: Annotated[QueryService, Depends(query_service)],
        flat: bool = False,
        user_id: Annotated[str | None, Query(pattern=USER_ID, max_length=128)] = None,
    ) -> TraceTree | TraceFlat:
        if flat:
            return await service.flat(run_id, user_id)
        return await service.trace(run_id, user_id)

    @router.get("/runs", response_model=RunList)
    async def get_runs(
        service: Annotated[QueryService, Depends(query_service)],
        limit: Annotated[int | None, Query()] = None,
        after: Annotated[str | None, Query(max_length=200)] = None,
        user_id: Annotated[str | None, Query(pattern=USER_ID, max_length=128)] = None,
    ) -> RunList:
        return await service.runs(limit, after, user_id)

    return router
