"""HTTP layer only: parse, validate, call a service, map errors. Internal endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from common.schemas import StudentProfile
from common.service import require_internal_token

from ..config import Settings
from ..models.profiles import Credentials, ProfileUpdate, Registered
from ..services.auth import AuthService
from ..services.profiles import ProfileService


def auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


def profile_service(request: Request) -> ProfileService:
    service: ProfileService = request.app.state.profiles
    return service


Auth = Annotated[AuthService, Depends(auth_service)]
Profiles = Annotated[ProfileService, Depends(profile_service)]


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

    return router
