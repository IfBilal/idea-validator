import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from common.config import ConfigError
from common.service import create_app
from common.tracing import Tracer

from .api.routes import make_router
from .config import Settings
from .config_llm import load_llm_config
from .config_sources import load_sources
from .services.cache import RedisCache
from .services.fetch import FetchService, MemoryRunBudget
from .services.fetcher import PinnedHttpxFetcher
from .services.llm import LlmService, MemoryLlmBudget, ModelAlias
from .services.llm_provider import LiteLlmProvider
from .services.metrics import PrometheusFetchMetrics
from .services.ratelimit import RedisTokenBucket
from .services.robots import RobotsChecker
from .services.robots_source import PolicyRobotsSource
from .services.url_policy import SystemResolver, UrlPolicy


def build_app(
    settings: Settings, service: FetchService | None = None, llm: LlmService | None = None
) -> FastAPI:
    """Wire M5. ``service`` can be injected (tests); otherwise it is built from the environment
    and ConfigMap at start-up, and missing configuration stops the service."""

    holder: dict[str, PrometheusFetchMetrics] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis: Redis | None = None
        active = service
        if active is None:
            if settings.redis_url is None:
                raise ConfigError("REDIS_URL is not set")
            sources = load_sources(settings.config_dir)
            redis = Redis.from_url(settings.redis_url.get_secret_value())
            policy = UrlPolicy(sources.allow_list, SystemResolver())
            fetcher = PinnedHttpxFetcher(sources.user_agent)
            cache = RedisCache(redis)
            active = FetchService(
                policy=policy,
                robots=RobotsChecker(
                    PolicyRobotsSource(policy, fetcher),
                    cache,
                    sources.user_agent,
                    set(sources.robots_exempt),
                ),
                bucket=RedisTokenBucket(redis),
                cache=cache,
                fetcher=fetcher,
                budget=MemoryRunBudget(settings.run_max_pages, settings.run_max_bytes),
                sources=sources,
                metrics=holder["metrics"],
                sleep=asyncio.sleep,
                clock=time.time,
            )
        active_llm = llm
        if active_llm is None and service is None:  # production: build from ConfigMap + Secret
            if settings.llm_api_key is None:
                raise ConfigError("LLM_API_KEY is not set")
            llm_cfg = load_llm_config(settings.config_dir)
            active_llm = LlmService(
                provider=LiteLlmProvider(settings.llm_api_key.get_secret_value()),
                budget=MemoryLlmBudget(settings.llm_run_max_tokens, settings.llm_run_max_cost_usd),
                aliases={
                    name: ModelAlias(a.model, a.host, a.fallback_usd_per_1k_tokens)
                    for name, a in llm_cfg.aliases.items()
                },
                allowed_hosts=llm_cfg.allowed_hosts,
                tracer=Tracer("tools"),
                timeout_s=settings.llm_timeout_s,
            )
        app.state.fetch = active
        app.state.llm = active_llm
        app.state.redis = redis
        try:
            yield
        finally:
            if redis is not None:
                await redis.aclose()

    async def redis_up() -> bool:
        redis = getattr(app.state, "redis", None)
        if redis is None:
            return service is not None
        return bool(await redis.ping())

    app = create_app(
        name="tools",
        module="M5",
        settings=settings,
        ready_checks={"redis": redis_up},
        lifespan=lifespan,
    )
    holder["metrics"] = PrometheusFetchMetrics(app.state.metrics_registry)
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
