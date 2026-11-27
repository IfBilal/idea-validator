"""HTTP layer only: authenticate, rate-limit, validate, call a service, return the result."""

import ipaddress
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from common.errors import RateLimited, Unauthorized
from common.logging import get_logger
from common.schemas import StudentProfile

from ..clients.profile import ProfileClient
from ..config import Settings
from ..models.auth import Credentials, ProfileBody, RegisterOut, TokenOut
from ..ratelimit import RateLimiter, fingerprint
from ..security import verify_token
from ..services.auth import AuthFlow

log = get_logger("gateway")


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The address rate limits are keyed on.

    With no trusted proxy the TCP peer is used and ``X-Forwarded-For`` is ignored. Behind
    ``n`` trusted proxies it is the entry ``n`` places from the right (the one the outermost
    trusted proxy saw), so anything a client prepends is ignored. Invalid values fall back to
    the peer.
    """
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    entries = [
        e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()
    ]
    if len(entries) < trusted_proxy_hops:
        return peer
    try:
        return str(ipaddress.ip_address(entries[-trusted_proxy_hops]))
    except ValueError:
        return peer


def make_router(
    settings: Settings,
    limiter: RateLimiter,
    auth: AuthFlow,
    profiles: ProfileClient,
    secret: str,
) -> APIRouter:
    def ip_of(request: Request) -> str:
        return client_ip(request, settings.trusted_proxy_hops)

    async def guarded(request: Request, kind: str, limit: int) -> None:
        try:
            await limiter.hit(kind, ip_of(request), limit)
        except RateLimited:
            log.warning("rate_limited", kind=kind, client=fingerprint(ip_of(request)))
            raise

    async def ip_limit(request: Request) -> None:
        await guarded(request, "ip", settings.ip_per_min)

    def current_user(request: Request) -> str:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise Unauthorized("missing bearer token")
            return verify_token(secret, token)
        except Unauthorized:
            log.warning("token_rejected", client=fingerprint(ip_of(request)))
            raise

    User = Annotated[str, Depends(current_user)]
    router = APIRouter(dependencies=[Depends(ip_limit)])

    @router.post("/auth/register", status_code=201, response_model=RegisterOut)
    async def register(body: Credentials, request: Request) -> RegisterOut:
        await guarded(request, "register", settings.register_per_min)
        return RegisterOut(user_id=await auth.register(body.email, body.password))

    @router.post("/auth/login", response_model=TokenOut)
    async def login(body: Credentials, request: Request) -> TokenOut:
        """Attempts are limited per IP; failures are also limited per email, so one account
        cannot be hammered from many addresses and legitimate logins never count against it."""
        await guarded(request, "login_ip", settings.login_per_min)
        email_key = fingerprint(body.email)
        await limiter.peek("login_fail_email", email_key, settings.login_per_min)
        try:
            return await auth.login(body.email, body.password)
        except Unauthorized:
            await limiter.hit("login_fail_email", email_key, settings.login_per_min)
            log.warning("auth_failed", client=fingerprint(ip_of(request)), email=email_key)
            raise

    @router.get("/profile", response_model=StudentProfile, response_model_exclude_none=True)
    async def get_profile(user_id: User) -> dict[str, Any]:
        return await profiles.get_profile(user_id)

    @router.put("/profile", response_model=StudentProfile, response_model_exclude_none=True)
    async def put_profile(body: ProfileBody, user_id: User) -> dict[str, Any]:
        return await profiles.put_profile(user_id, body.model_dump())

    return router
