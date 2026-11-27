"""HTTP layer only: authenticate, rate-limit, validate, call a service, return the result."""

import ipaddress
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import StreamingResponse

from common.errors import RateLimited, Unauthorized, ValidationFailed
from common.logging import get_logger
from common.schemas import StudentProfile

from ..clients.profile import ProfileClient
from ..config import Settings
from ..models.auth import Credentials, ProfileBody, RegisterOut, TokenOut
from ..models.ideas import Comparison, IdeaPage, VersionCard, VersionSummary
from ..models.runs import (
    IDEMPOTENCY_KEY_PATTERN,
    RUN_ID_PATTERN,
    AnswerBody,
    ApproveBody,
    ChallengeBody,
    DiscoverBody,
    RunAccepted,
    RunView,
    StepAccepted,
    ValidateAccepted,
    ValidateBody,
    WhatIfBody,
)
from ..ratelimit import RateLimiter, fingerprint
from ..security import verify_claims
from ..services.auth import AuthFlow
from ..services.ideas import IdeaFlow
from ..services.runs import RunFlow
from ..services.streaming import stream_deadline

MIN_COMPARE, MAX_COMPARE = 2, 5

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
    runs: RunFlow,
    ideas: IdeaFlow,
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
        """The token's user; its expiry is kept for streams, which must not outlive it."""
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise Unauthorized("missing bearer token")
            user, request.state.token_exp = verify_claims(secret, token)
            return user
        except Unauthorized:
            log.warning("token_rejected", client=fingerprint(ip_of(request)))
            raise

    async def authed(request: Request) -> str:
        user_id = current_user(request)
        await user_limit("user_req", settings.user_per_min, user_id)
        return user_id

    User = Annotated[str, Depends(authed)]
    IdemKey = Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
    ]
    RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN)]
    IdeaId = Annotated[uuid.UUID, Path()]
    Version = Annotated[int, Path(ge=1)]

    async def user_limit(kind: str, limit: int, user_id: str) -> None:
        try:
            await limiter.hit(kind, user_id, limit)
        except RateLimited:
            log.warning("rate_limited", kind=kind, user=fingerprint(user_id))
            raise

    def parse_compare_ids(raw: str) -> list[str]:
        parts = [p.strip() for p in raw.split(",")]
        try:
            ids = [str(uuid.UUID(p)) for p in parts]
        except ValueError:
            raise ValidationFailed("ids must be comma-separated idea ids") from None
        if not MIN_COMPARE <= len(ids) <= MAX_COMPARE or len(set(ids)) != len(ids):
            raise ValidationFailed(f"give {MIN_COMPARE} to {MAX_COMPARE} different idea ids")
        return ids

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

    # ---- runs ----------------------------------------------------------------------------
    @router.post("/validate", status_code=202, response_model=ValidateAccepted)
    async def validate(
        body: ValidateBody, user_id: User, idempotency_key: IdemKey = None
    ) -> ValidateAccepted:
        await user_limit("user_runs", settings.user_runs_per_min, user_id)
        return await runs.validate(user_id, body, idempotency_key)

    @router.post("/discover", status_code=202, response_model=RunAccepted)
    async def discover(
        body: DiscoverBody, user_id: User, idempotency_key: IdemKey = None
    ) -> RunAccepted:
        await user_limit("user_runs", settings.user_runs_per_min, user_id)
        return await runs.discover(user_id, body, idempotency_key)

    @router.get("/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: RunId, user_id: User) -> RunView:
        return await runs.get(user_id, run_id)

    @router.post("/runs/{run_id}/answer", status_code=202, response_model=StepAccepted)
    async def answer(run_id: RunId, body: AnswerBody, user_id: User) -> StepAccepted:
        return await runs.answer(user_id, run_id, body)

    @router.post("/runs/{run_id}/approve", status_code=202, response_model=StepAccepted)
    async def approve(run_id: RunId, body: ApproveBody, user_id: User) -> StepAccepted:
        return await runs.approve(user_id, run_id, body)

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: RunId, request: Request, user_id: User) -> StreamingResponse:
        """Server-sent events. A browser ``EventSource`` cannot send ``Authorization``, so the UI
        reads this with ``fetch`` and a stream reader (see the README)."""
        await user_limit("user_sse", settings.sse_per_min, user_id)
        lifetime = stream_deadline(request.state.token_exp, time.time(), settings.sse_max_s)
        frames = await runs.open_events(
            user_id, run_id, request.headers.get("last-event-id"), lifetime
        )
        return StreamingResponse(
            frames,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ---- ideas ---------------------------------------------------------------------------
    @router.get("/ideas", response_model=IdeaPage)
    async def list_ideas(
        user_id: User,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ) -> IdeaPage:
        return await ideas.page(user_id, limit, cursor)

    @router.get("/ideas/{idea_id}/versions", response_model=list[VersionSummary])
    async def idea_versions(idea_id: IdeaId, user_id: User) -> list[VersionSummary]:
        return await ideas.versions(user_id, str(idea_id))

    @router.get("/ideas/{idea_id}/versions/{version}", response_model=VersionCard)
    async def idea_version(idea_id: IdeaId, version: Version, user_id: User) -> VersionCard:
        return await ideas.version(user_id, str(idea_id), version)

    @router.get("/compare", response_model=Comparison)
    async def compare(user_id: User, ids: Annotated[str, Query(max_length=300)]) -> Comparison:
        return await ideas.compare(user_id, parse_compare_ids(ids))

    @router.post("/ideas/{idea_id}/challenge", status_code=202, response_model=RunAccepted)
    async def challenge(
        idea_id: IdeaId, body: ChallengeBody, user_id: User, idempotency_key: IdemKey = None
    ) -> RunAccepted:
        await user_limit("user_runs", settings.user_runs_per_min, user_id)
        return await runs.challenge(user_id, str(idea_id), body, idempotency_key)

    @router.post("/ideas/{idea_id}/whatif", status_code=202, response_model=RunAccepted)
    async def whatif(
        idea_id: IdeaId, body: WhatIfBody, user_id: User, idempotency_key: IdemKey = None
    ) -> RunAccepted:
        await user_limit("user_runs", settings.user_runs_per_min, user_id)
        return await runs.whatif(user_id, str(idea_id), body, idempotency_key)

    return router
