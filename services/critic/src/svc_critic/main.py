from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI

from common.service import create_app

from .api.routes import make_router
from .clients.knowledge_base import KnowledgeBaseClient, UnconfiguredKnowledgeBase
from .config import Settings
from .services.approval import BudgetBook
from .services.claims import ClaimChecker
from .services.entailment import EntailmentJudge, LexicalOverlapJudge
from .services.objections import ObjectionFilter
from .services.rail import InputRail, RailLimits


def build_app(
    settings: Settings,
    *,
    kb: KnowledgeBaseClient | UnconfiguredKnowledgeBase | None = None,
    judge: EntailmentJudge | None = None,
) -> FastAPI:
    if kb is None:
        if settings.kb_url:
            token = settings.internal_token.get_secret_value() if settings.internal_token else None
            kb = KnowledgeBaseClient(
                settings.kb_url,
                timeout_s=settings.kb_timeout_s,
                max_retries=settings.kb_max_retries,
                internal_token=token,
            )
        else:
            kb = UnconfiguredKnowledgeBase()
    judge = judge or LexicalOverlapJudge(settings.judge_threshold, settings.max_quote_chars)
    checker = ClaimChecker(
        kb, judge, max_claims=settings.max_claims, max_quote_chars=settings.max_quote_chars
    )
    budgets = BudgetBook(settings.config_dir, Decimal(str(settings.hard_cap_multiplier)))

    async def budgets_loadable() -> bool:
        budgets.get()
        return True

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await kb.aclose()

    app = create_app(
        name="critic",
        module="M12",
        settings=settings,
        ready_checks={"knowledge_base": kb.is_ready, "budgets": budgets_loadable},
        lifespan=lifespan,
    )
    app.state.checker = checker
    app.state.objection_filter = ObjectionFilter(checker, max_accepted=settings.max_objections)
    app.state.rail = InputRail(
        RailLimits(
            idea=settings.rail_max_idea_chars,
            answer=settings.rail_max_answer_chars,
            niche=settings.rail_max_niche_chars,
        )
    )
    app.state.budgets = budgets
    app.include_router(make_router(settings))
    return app


app = build_app(Settings())
