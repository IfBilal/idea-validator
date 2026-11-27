from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_client import Counter

from common.service import create_app

from .api.routes import make_router
from .clients.llm import LlmClient
from .config import Settings
from .services.prompt import PromptBuilder
from .services.scoring import ScoringService


def build_app(
    settings: Settings, *, llm_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    llm: LlmClient | None = None
    if settings.llm_url:
        llm = LlmClient(
            settings.llm_url,
            model=settings.model_name,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            internal_token=(
                settings.internal_token.get_secret_value() if settings.internal_token else None
            ),
            transport=llm_transport,
        )

    async def llm_up() -> bool:
        return llm is not None and await llm.ping()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if llm is not None:
                await llm.aclose()

    app = create_app(
        name="scorer",
        module="M10",
        settings=settings,
        ready_checks={"llm": llm_up},
        lifespan=lifespan,
    )
    fallbacks = Counter(
        "scorer_fallback_total",
        "Scores that fell back to an abstention",
        ["reason"],
        registry=app.state.metrics_registry,
    )
    app.state.scoring = (
        ScoringService(
            llm,
            PromptBuilder(settings.rubrics_dir, settings.prompts_dir),
            model_name=settings.model_name,
            on_fallback=lambda reason: fallbacks.labels(reason).inc(),
        )
        if llm is not None
        else None
    )
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
