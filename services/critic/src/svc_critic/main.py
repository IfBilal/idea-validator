from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI

from common.limits import BodyLimitMiddleware
from common.service import create_app

from .api.routes import make_router
from .clients.knowledge_base import KnowledgeBaseClient, UnconfiguredKnowledgeBase
from .clients.llm import LlmClient
from .config import Settings
from .services.approval import BudgetBook
from .services.claims import ClaimChecker
from .services.entailment import EntailmentJudge, LexicalOverlapJudge
from .services.llm_judge import LlmEntailmentJudge
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
    llm: LlmClient | None = None
    if judge is None:
        judge = LexicalOverlapJudge(settings.judge_threshold, settings.max_quote_chars)
        if settings.tools_url and settings.llm_judge_enabled is not False:
            token = settings.internal_token.get_secret_value() if settings.internal_token else None
            llm = LlmClient(
                settings.tools_url,
                model=settings.llm_judge_model,
                timeout_s=settings.llm_judge_timeout_s,
                internal_token=token,
            )
            judge = LlmEntailmentJudge(
                judge,
                llm,
                settings.prompts_dir,
                max_claims=settings.llm_judge_max_claims,
                max_evidence_chars=settings.llm_judge_max_evidence_chars,
                max_quote_chars=settings.max_quote_chars,
            )
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
            if llm is not None:
                await llm.aclose()

    app = create_app(
        name="critic",
        module="M12",
        settings=settings,
        ready_checks={"knowledge_base": kb.is_ready, "budgets": budgets_loadable},
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
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
