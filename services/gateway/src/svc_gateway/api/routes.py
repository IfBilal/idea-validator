"""HTTP layer only: authenticate, rate-limit, validate, call a service, return the result."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from common.errors import Unauthorized

from ..clients.profile import ProfileClient
from ..config import Settings
from ..models.auth import Credentials, ProfileBody, RegisterOut, TokenOut
from ..ratelimit import RateLimiter, fingerprint
from ..security import verify_token
from ..services.auth import AuthFlow


def client_ip(request: Request, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def make_router(
    settings: Settings,
    limiter: RateLimiter,
    auth: AuthFlow,
    profiles: ProfileClient,
    secret: str,
) -> APIRouter:
    async def ip_limit(request: Request) -> None:
        await limiter.hit(
            "ip", client_ip(request, settings.trust_forwarded_for), settings.ip_per_min
        )

    def current_user(request: Request) -> str:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise Unauthorized("missing bearer token")
        return verify_token(secret, token)

    User = Annotated[str, Depends(current_user)]
    router = APIRouter(dependencies=[Depends(ip_limit)])

    @router.post("/auth/register", status_code=201, response_model=RegisterOut)
    async def register(body: Credentials, request: Request) -> RegisterOut:
        ip = client_ip(request, settings.trust_forwarded_for)
        await limiter.hit("register", ip, settings.register_per_min)
        return RegisterOut(user_id=await auth.register(body.email, body.password))

    @router.post("/auth/login", response_model=TokenOut)
    async def login(body: Credentials, request: Request) -> TokenOut:
        ip = client_ip(request, settings.trust_forwarded_for)
        await limiter.hit("login_ip", ip, settings.login_per_min)
        await limiter.hit("login_email", fingerprint(body.email), settings.login_per_min)
        return await auth.login(body.email, body.password)

    @router.get("/profile")
    async def get_profile(user_id: User) -> dict[str, Any]:
        return await profiles.get_profile(user_id)

    @router.put("/profile")
    async def put_profile(body: ProfileBody, user_id: User) -> dict[str, Any]:
        return await profiles.put_profile(user_id, body.model_dump())

    return router
