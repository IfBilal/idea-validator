"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from common.errors import ValidationFailed
from common.schemas import StudentProfile
from common.service import require_internal_token

from ..config import Settings
from ..models.profiles import Credentials, ProfileUpdate, Registered
from ..models.storage import (
    ApprovalDecision,
    ApprovalOut,
    CacheResult,
    CacheValue,
    CompareRow,
    IdeaCreated,
    IdeaPage,
    NewApproval,
    NewIdea,
    NewVersion,
    PutVersion,
    RunBody,
    RunOut,
    StoreVerdict,
    VerdictStored,
    VersionDetail,
    VersionPut,
    VersionSummary,
)
from ..services.auth import AuthService
from ..services.cache import CacheService
from ..services.ideas import IdeaService
from ..services.profiles import ProfileService
from ..services.runs import ApprovalService, RunService
from ..services.verdicts import VerdictService

MAX_LIST_STATES = 10
STATE_NAME = re.compile(r"[a-z_]{1,40}")


def auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


def profile_service(request: Request) -> ProfileService:
    service: ProfileService = request.app.state.profiles
    return service


def _state(name: str) -> object:
    def dependency(request: Request) -> object:
        return getattr(request.app.state, name)

    return dependency


Auth = Annotated[AuthService, Depends(auth_service)]
Profiles = Annotated[ProfileService, Depends(profile_service)]
Ideas = Annotated[IdeaService, Depends(_state("ideas"))]
Verdicts = Annotated[VerdictService, Depends(_state("verdicts"))]
Runs = Annotated[RunService, Depends(_state("runs"))]
Approvals = Annotated[ApprovalService, Depends(_state("approvals"))]
Cache = Annotated[CacheService, Depends(_state("cache"))]
CacheKey = Path(pattern=r"^[A-Za-z0-9_.:@#-]{1,300}$")


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_token(settings))])

    @router.post("/users", status_code=201, response_model=Registered)
    async def register(body: Credentials, auth: Auth) -> Registered:
        return Registered(user_id=str(await auth.register(body.email, body.password)))

    @router.post("/auth/login", response_model=Registered)
    async def login(body: Credentials, auth: Auth) -> Registered:
        return Registered(user_id=str(await auth.login(body.email, body.password)))

    @router.get(
        "/profiles/{user_id}", response_model=StudentProfile, response_model_exclude_none=True
    )
    async def get_profile(
        user_id: uuid.UUID, profiles: Profiles, version: Annotated[int | None, Query(ge=1)] = None
    ) -> StudentProfile:
        return await profiles.get(user_id, version)

    @router.put(
        "/profiles/{user_id}", response_model=StudentProfile, response_model_exclude_none=True
    )
    async def put_profile(
        user_id: uuid.UUID, body: ProfileUpdate, profiles: Profiles
    ) -> StudentProfile:
        return await profiles.put(user_id, body)

    @router.post("/ideas", status_code=201, response_model=IdeaCreated)
    async def create_idea(body: NewIdea, ideas: Ideas) -> IdeaCreated:
        return await ideas.create(body)

    @router.get("/ideas", response_model=IdeaPage)
    async def list_ideas(
        ideas: Ideas,
        user_id: Annotated[uuid.UUID, Query()],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> IdeaPage:
        return await ideas.page(user_id, limit, cursor)

    @router.post("/ideas/{idea_id}/versions", status_code=201, response_model=IdeaCreated)
    async def add_version(idea_id: uuid.UUID, body: NewVersion, ideas: Ideas) -> IdeaCreated:
        return await ideas.add_version(idea_id, body)

    @router.get("/ideas/{idea_id}/versions", response_model=list[VersionSummary])
    async def list_versions(
        idea_id: uuid.UUID, ideas: Ideas, user_id: Annotated[uuid.UUID, Query()]
    ) -> list[VersionSummary]:
        return await ideas.versions(idea_id, user_id)

    @router.get("/ideas/{idea_id}/versions/{version}", response_model=VersionDetail)
    async def get_version(
        idea_id: uuid.UUID,
        version: Annotated[int, Path(ge=1)],
        ideas: Ideas,
        user_id: Annotated[uuid.UUID, Query()],
    ) -> VersionDetail:
        return await ideas.detail(idea_id, user_id, version)

    @router.put("/ideas/{idea_id}/versions/{version}", response_model=VersionPut)
    async def put_version(
        idea_id: uuid.UUID,
        version: Annotated[int, Path(ge=1)],
        body: PutVersion,
        ideas: Ideas,
    ) -> VersionPut:
        return await ideas.put_version(idea_id, version, body)

    @router.post("/verdicts", status_code=201, response_model=VerdictStored)
    async def store_verdict(body: StoreVerdict, verdicts: Verdicts) -> VerdictStored:
        return await verdicts.store(body)

    @router.get("/compare", response_model=list[CompareRow])
    async def compare(
        ideas: Ideas,
        user_id: Annotated[uuid.UUID, Query()],
        idea_ids: Annotated[list[uuid.UUID], Query(min_length=2, max_length=5)],
    ) -> list[CompareRow]:
        return await ideas.compare(user_id, idea_ids)

    @router.get("/runs", response_model=list[RunOut])
    async def list_runs(
        runs: Runs,
        state: Annotated[str, Query(min_length=1, max_length=400)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        after: Annotated[str, Query(max_length=80)] = "",
    ) -> list[RunOut]:
        """``state`` is a comma list such as ``running,awaiting_input``; paged by run id."""
        states = state.split(",")
        if len(states) > MAX_LIST_STATES or not all(STATE_NAME.fullmatch(s) for s in states):
            raise ValidationFailed("state must be up to 10 lower-case state names")
        return await runs.list_by_state(states, limit, after or None)

    @router.get("/runs/{run_id}", response_model=RunOut)
    async def get_run(
        run_id: Annotated[str, Path(max_length=80)],
        runs: Runs,
        user_id: Annotated[uuid.UUID, Query()],
    ) -> RunOut:
        return await runs.get(run_id, user_id)

    @router.put("/runs/{run_id}", response_model=RunOut)
    async def put_run(
        run_id: Annotated[str, Path(max_length=80)], body: RunBody, runs: Runs
    ) -> RunOut:
        return await runs.put(run_id, body)

    @router.post("/approvals", status_code=201, response_model=ApprovalOut)
    async def create_approval(body: NewApproval, approvals: Approvals) -> ApprovalOut:
        return await approvals.create(body)

    @router.put("/approvals/{approval_id}", response_model=ApprovalOut)
    async def decide_approval(
        approval_id: uuid.UUID, body: ApprovalDecision, approvals: Approvals
    ) -> ApprovalOut:
        return await approvals.decide(approval_id, body)

    @router.get("/cache/verdict/{key}", response_model=CacheResult)
    async def cache_get(key: Annotated[str, CacheKey], cache: Cache) -> CacheResult:
        return await cache.get(key)

    @router.put("/cache/verdict/{key}", status_code=204)
    async def cache_put(key: Annotated[str, CacheKey], body: CacheValue, cache: Cache) -> None:
        await cache.put(key, body.value, body.ttl_s)

    return router
