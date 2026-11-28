from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import BlockingConnectionPool, Redis

from common.config import ConfigError
from common.http import ServiceClient
from common.service import create_app

from .api.routes import make_router
from .clients.agentops import AgentOpsClient
from .clients.coordination import (
    IdempotencyStore,
    RedisIdempotencyStore,
    RedisStreamGate,
    StreamGate,
)
from .clients.critic import InputRail
from .clients.events import EventBus, RedisEventBus
from .clients.ideas import IdeaClient
from .clients.orchestrator import OrchestratorClient
from .clients.profile import ProfileClient
from .config import Settings
from .middleware import SECURITY_HEADERS, BodyLimitMiddleware, SecurityHeadersMiddleware
from .ratelimit import RateLimiter, RedisRateLimiter
from .security import MIN_SECRET_LENGTH
from .services.auth import AuthFlow
from .services.ideas import IdeaFlow
from .services.runs import RunFlow, RunFlowConfig

log = structlog.get_logger("gateway")


def build_app(
    settings: Settings,
    limiter: RateLimiter | None = None,
    profiles: ProfileClient | None = None,
    ideas: IdeaClient | None = None,
    orchestrator: OrchestratorClient | None = None,
    events: EventBus | None = None,
    store: IdempotencyStore | None = None,
    gate: StreamGate | None = None,
    agentops: AgentOpsClient | None = None,
    rail: InputRail | None = None,
) -> FastAPI:
    """Wire the gateway. Every dependency can be injected (tests); otherwise it is built from the
    environment at start-up, and missing configuration stops the service."""
    holder: dict[str, object] = {}

    if (
        settings.jwt_secret is None
        or len(settings.jwt_secret.get_secret_value()) < MIN_SECRET_LENGTH
    ):
        secret = None
    else:
        secret = settings.jwt_secret.get_secret_value()

    def connect(url: str | None, name: str, token: str | None) -> ServiceClient:
        if url is None:
            raise ConfigError(f"{name} is not set")
        return ServiceClient(url, timeout_s=settings.upstream_timeout_s, internal_token=token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if secret is None:
            raise ConfigError(f"JWT_SECRET must be set and at least {MIN_SECRET_LENGTH} characters")
        token = settings.internal_token.get_secret_value() if settings.internal_token else None
        redis: Redis | None = None
        event_redis: Redis | None = None
        owned: list[ServiceClient] = []
        active_limiter, active_events = limiter, events
        active_store, active_gate = store, gate
        active_profiles, active_ideas, active_orch = profiles, ideas, orchestrator
        if None in (active_limiter, active_events, active_store, active_gate):
            if settings.redis_url is None:
                raise ConfigError("REDIS_URL is not set")
            url = settings.redis_url.get_secret_value()
            redis = Redis.from_url(url)
            active_limiter = active_limiter or RedisRateLimiter(redis)
            active_store = active_store or RedisIdempotencyStore(redis)
            active_gate = active_gate or RedisStreamGate(redis)
            if active_events is None:
                # pub/sub holds one connection per stream: a bounded pool of its own, so streams
                # can never starve the rate limiter, and a full pool refuses instead of growing
                pool = BlockingConnectionPool.from_url(
                    url, max_connections=settings.sse_pool_size, timeout=2.0
                )
                event_redis = Redis(connection_pool=pool)
                active_events = RedisEventBus(event_redis)
        if active_profiles is None or active_ideas is None:
            m2 = connect(settings.profile_url, "PROFILE_URL", token)
            owned.append(m2)
            active_profiles = active_profiles or ProfileClient(m2)
            active_ideas = active_ideas or IdeaClient(m2)
        if active_orch is None:
            m3 = connect(settings.orchestrator_url, "ORCHESTRATOR_URL", token)
            owned.append(m3)
            active_orch = OrchestratorClient(m3)
        active_agentops = agentops
        if active_agentops is None and settings.agentops_url:
            m13 = connect(settings.agentops_url, "AGENTOPS_URL", token)
            owned.append(m13)
            active_agentops = AgentOpsClient(m13, settings.max_trace_bytes)
        active_rail = rail
        if active_rail is None and settings.critic_url:
            m12 = connect(settings.critic_url, "CRITIC_URL", token)
            owned.append(m12)
            active_rail = InputRail(m12)
        if active_rail is None and settings.require_input_rail:
            raise ConfigError("CRITIC_URL is not set but REQUIRE_INPUT_RAIL is on")
        if active_rail is None:
            log.warning(
                "input_rail_disabled", reason="CRITIC_URL is not set: user text is not pre-checked"
            )
        holder.update(redis=redis, profiles=active_profiles, orchestrator=active_orch)
        auth = AuthFlow(active_profiles, settings, secret)
        assert active_limiter and active_events and active_store and active_gate
        run_flow = RunFlow(
            active_ideas,
            active_orch,
            active_events,
            active_store,
            active_gate,
            RunFlowConfig(
                heartbeat_s=settings.sse_heartbeat_s,
                stream_max_s=settings.sse_max_s,
                max_frame_bytes=settings.sse_max_frame_bytes,
                streams_per_user=settings.sse_streams_per_user,
                streams_global=settings.sse_streams_global,
            ),
            active_agentops,
            active_rail,
        )
        router = make_router(
            settings,
            active_limiter,
            auth,
            active_profiles,
            secret,
            run_flow,
            IdeaFlow(active_ideas),
        )
        app.include_router(router)
        try:
            yield
        finally:
            for client in owned:
                await client.aclose()
            for opened in (redis, event_redis):
                if opened is not None:
                    await opened.aclose()

    async def profile_up() -> bool:
        active = holder.get("profiles")
        return isinstance(active, ProfileClient) and await active.ready()

    async def orchestrator_up() -> bool:
        active = holder.get("orchestrator")
        return isinstance(active, OrchestratorClient) and await active.ready()

    async def redis_up() -> bool:
        active = holder.get("redis")
        if isinstance(active, Redis):
            return bool(await active.ping())
        return limiter is not None  # an injected limiter has nothing to ping

    app = create_app(
        name="gateway",
        module="M1",
        settings=settings,
        ready_checks={"redis": redis_up, "profile": profile_up, "orchestrator": orchestrator_up},
        lifespan=lifespan,
        error_headers={k.decode(): v.decode() for k, v in SECURITY_HEADERS.items()},
    )
    # later additions wrap earlier ones: the body limit is innermost, so even its 413 gets headers
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.body_limit_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.ui_origin] if settings.ui_origin else [],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "Idempotency-Key"],
        expose_headers=["Retry-After", "X-Request-Id"],
        allow_credentials=False,
        max_age=600,
    )
    return app


app = build_app(Settings())
