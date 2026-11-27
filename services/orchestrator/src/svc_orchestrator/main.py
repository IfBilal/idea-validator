from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from redis.asyncio import Redis

from common.budget import BudgetLedger
from common.config import ConfigError
from common.service import create_app
from common.tracing import Tracer

from .api.routes import make_router
from .clients.factory import build_clients
from .config import Settings
from .repositories.checkpoints import postgres_saver
from .repositories.events import RedisPublisher, RunEmitter
from .services.run_config import load_run_config
from .services.run_context import RunContext
from .services.runs import RunService


def service_urls(settings: Settings) -> dict[str, str]:
    return {
        "profile": settings.profile_url,
        "kb": settings.kb_url,
        "analyst": settings.analyst_url,
        "scouts": settings.scouts_url,
        "validators": settings.validators_url,
        "verdict": settings.verdict_url,
        "critic": settings.critic_url,
    }


def build_app(settings: Settings, *, service: RunService | None = None) -> FastAPI:
    """``service`` is injected by tests; in production the lifespan wires Redis and Postgres."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            if service is not None:
                app.state.runs = service
            else:
                app.state.runs = await _wire(settings, app, stack)
            try:
                yield
            finally:
                await app.state.runs.aclose()

    async def redis_up() -> bool:
        redis: Redis | None = getattr(app.state, "redis", None)
        return redis is not None and bool(await redis.ping())

    ready = {} if service is not None else {"redis": redis_up}
    app = create_app(
        name="orchestrator", module="M3", settings=settings, ready_checks=ready, lifespan=lifespan
    )
    if service is not None:
        app.state.runs = service  # also available when the lifespan is not run (ASGITransport)
    app.include_router(make_router(settings))
    return app


async def _wire(settings: Settings, app: FastAPI, stack: AsyncExitStack) -> RunService:
    if settings.redis_url is None:
        raise ConfigError("REDIS_URL is not set")
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    app.state.redis = redis
    stack.push_async_callback(redis.aclose)
    config = load_run_config("validate", config_dir=settings.config_dir)
    token = settings.internal_token.get_secret_value() if settings.internal_token else None
    clients = build_clients(service_urls(settings), config, internal_token=token)
    stack.push_async_callback(clients.aclose)
    tracer = Tracer("orchestrator")
    publisher = RedisPublisher(redis)

    def context(run_id: str) -> RunContext:
        return RunContext(
            run_id=run_id,
            config=config,
            ledger=BudgetLedger(redis, run_id, config.limits),
            tracer=tracer,
            emitter=RunEmitter(run_id, publisher),
            clients=clients,
        )

    saver: BaseCheckpointSaver[Any]
    if settings.database_url is not None:
        saver = await stack.enter_async_context(
            postgres_saver(settings.database_url.get_secret_value())
        )
    elif settings.environment == "production":
        raise ConfigError("DATABASE_URL is required in production")
    else:
        saver = MemorySaver()  # runs do not survive a restart without DATABASE_URL
    return RunService(
        context_factory=context,
        checkpointer=saver,
        config=config,
        question_timeout_s=settings.question_timeout_s,
    )


app = build_app(Settings())
