"""HTTP layer only: validate, call the runner, map errors. ``/scout`` is internal."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from common.errors import Conflict, NotFound, Unavailable, UpstreamTimeout, ValidationFailed
from common.service import require_internal_token
from svc_scouts.config import Settings
from svc_scouts.models.runs import RunReport
from svc_scouts.services.errors import AlreadyRunning, UnknownSource
from svc_scouts.services.runner import ScoutRunner


class ScoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=40)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


def runner_of(request: Request) -> ScoutRunner | None:
    runner: ScoutRunner | None = request.app.state.runner
    return runner


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_internal_token(settings))])

    @router.post("/scout", response_model=RunReport)
    async def scout(
        body: ScoutRequest, runner: Annotated[ScoutRunner | None, Depends(runner_of)]
    ) -> RunReport:
        """One bounded, single-flight run of one source (deep search on demand)."""
        if runner is None:
            raise Unavailable("scouts is not configured (database, tools URL or sources)")
        if body.limit is not None and body.limit > settings.scout_max_limit:
            raise ValidationFailed(
                "limit is above the allowed maximum", {"max_limit": settings.scout_max_limit}
            )
        try:
            async with asyncio.timeout(settings.scout_timeout_s):
                return await runner.run(body.source, limit=body.limit, run_id=body.run_id)
        except UnknownSource as exc:
            raise NotFound(str(exc)) from exc
        except AlreadyRunning as exc:
            raise Conflict(str(exc)) from exc
        except TimeoutError as exc:
            raise UpstreamTimeout("the run took longer than the allowed time") from exc

    return router
