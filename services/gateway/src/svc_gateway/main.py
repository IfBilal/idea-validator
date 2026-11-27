from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from common.config import ConfigError
from common.http import ServiceClient
from common.service import create_app

from .api.routes import make_router
from .clients.profile import ProfileClient
from .config import Settings
from .middleware import BodyLimitMiddleware, SecurityHeadersMiddleware
from .ratelimit import RateLimiter, RedisRateLimiter
from .security import MIN_SECRET_LENGTH
from .services.auth import AuthFlow


def build_app(
    settings: Settings,
    limiter: RateLimiter | None = None,
    profiles: ProfileClient | None = None,
) -> FastAPI:
    """Wire the gateway. ``limiter`` and ``profiles`` can be injected (tests); otherwise they are
    built from the environment at start-up, and missing configuration stops the service."""
    holder: dict[str, object] = {}

    if (
        settings.jwt_secret is None
        or len(settings.jwt_secret.get_secret_value()) < MIN_SECRET_LENGTH
    ):
        secret = None
    else:
        secret = settings.jwt_secret.get_secret_value()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if secret is None:
            raise ConfigError(f"JWT_SECRET must be set and at least {MIN_SECRET_LENGTH} characters")
        redis: Redis | None = None
        client: ServiceClient | None = None
        active_limiter, active_profiles = limiter, profiles
        if active_limiter is None:
            if settings.redis_url is None:
                raise ConfigError("REDIS_URL is not set")
            redis = Redis.from_url(settings.redis_url.get_secret_value())
            active_limiter = RedisRateLimiter(redis)
        if active_profiles is None:
            if settings.profile_url is None:
                raise ConfigError("PROFILE_URL is not set")
            token = settings.internal_token.get_secret_value() if settings.internal_token else None
            client = ServiceClient(
                settings.profile_url,
                timeout_s=settings.upstream_timeout_s,
                internal_token=token,
            )
            active_profiles = ProfileClient(client)
        holder["redis"], holder["profiles"] = redis, active_profiles
        auth = AuthFlow(active_profiles, settings, secret)
        app.include_router(make_router(settings, active_limiter, auth, active_profiles, secret))
        try:
            yield
        finally:
            if client is not None:
                await client.aclose()
            if redis is not None:
                await redis.aclose()

    async def profile_up() -> bool:
        active = holder.get("profiles")
        return isinstance(active, ProfileClient) and await active.ready()

    async def redis_up() -> bool:
        active = holder.get("redis")
        if isinstance(active, Redis):
            return bool(await active.ping())
        return limiter is not None  # an injected limiter has nothing to ping

    app = create_app(
        name="gateway",
        module="M1",
        settings=settings,
        ready_checks={"redis": redis_up, "profile": profile_up},
        lifespan=lifespan,
    )
    # later additions wrap earlier ones: the body limit is innermost, so even its 413 gets headers
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.body_limit_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.ui_origin] if settings.ui_origin else [],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )
    return app


app = build_app(Settings())
