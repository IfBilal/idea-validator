from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_client import Counter

from common.service import create_app

from .api.routes import make_router
from .clients.llm import LlmClient
from .clients.local import LocalLlamaClient
from .config import Settings
from .services.prompt import PromptBuilder
from .services.scoring import ScoringService


def build_app(
    settings: Settings,
    *,
    llm_transport: httpx.AsyncBaseTransport | None = None,
    local_transport: httpx.AsyncBaseTransport | None = None,
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
    local: LocalLlamaClient | None = None
    if settings.local_llm_url:
        local = LocalLlamaClient(
            settings.local_llm_url,
            model=settings.local_model_name,
            timeout_s=settings.local_timeout_s,
            transport=local_transport,
        )

    async def llm_up() -> bool:
        return llm is not None and await llm.ping()

    async def local_up() -> bool:
        return local is not None and await local.ready()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if llm is not None:
                await llm.aclose()
            if local is not None:
                await local.aclose()

    # Local-first: ready means the fine-tuned model is loaded and warm (hosted is only a fallback).
    checks = {"local_model": local_up} if local is not None else {"llm": llm_up}
    app = create_app(
        name="scorer",
        module="M10",
        settings=settings,
        ready_checks=checks,
        lifespan=lifespan,
    )
    fallbacks = Counter(
        "scorer_fallback_total",
        "Scores that fell back to the hosted model or to an abstention",
        ["reason"],
        registry=app.state.metrics_registry,
    )
    primary = local if local is not None else llm
    app.state.scoring = (
        ScoringService(
            primary,
            PromptBuilder(settings.rubrics_dir, settings.prompts_dir),
            model_name=settings.local_model_name if local is not None else settings.model_name,
            fallback_llm=llm if local is not None else None,
            fallback_model_name=settings.model_name,
            on_fallback=lambda reason: fallbacks.labels(reason).inc(),
        )
        if primary is not None
        else None
    )
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
