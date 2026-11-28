"""Wires settings into clients and agent services once per process (no globals, no fastapi)."""

from dataclasses import dataclass

import httpx

from common.errors import DomainError, UpstreamError
from common.http import ServiceClient
from common.logging import get_logger
from common.trace_sink import tracer_for

from .clients.analyst import AnalystClient
from .clients.kb import KnowledgeBaseClient
from .clients.llm import LlmClient
from .config import Settings
from .services.changes import ChangeService
from .services.gap_analyst import GapAnalystService
from .services.ideator import IdeatorService
from .services.prompts import load_prompt
from .services.rewriter import RewriterService
from .services.runner import AgentRunner
from .services.skeptic import SkepticService

PROMPT_NAMES = ("ideator", "gap_analyst", "skeptic", "change_suggester", "idea_rewriter")
log = get_logger("ideation")


@dataclass(frozen=True)
class Transports:
    """Test seams: replace the network for M5, M6 and M7."""

    llm: httpx.AsyncBaseTransport | None = None
    kb: httpx.AsyncBaseTransport | None = None
    analyst: httpx.AsyncBaseTransport | None = None


class Agents:
    def __init__(
        self,
        ideator: IdeatorService,
        gap: GapAnalystService,
        skeptic: SkepticService,
        changes: ChangeService,
        rewriter: RewriterService,
    ) -> None:
        self.ideator = ideator
        self.gap_analyst = gap
        self.skeptic = skeptic
        self.changes = changes
        self.rewriter = rewriter


class Runtime:
    def __init__(self, settings: Settings, transports: Transports | None = None) -> None:
        self._settings = settings
        transports = transports or Transports()
        token = settings.internal_token.get_secret_value() if settings.internal_token else None

        def client(
            url: str | None, timeout: float, transport: httpx.AsyncBaseTransport | None
        ) -> ServiceClient | None:
            if not url:
                return None
            return ServiceClient(
                url,
                timeout_s=timeout,
                max_retries=settings.max_retries,
                internal_token=token,
                transport=transport,
            )

        self._tools = client(settings.tools_url, settings.llm_timeout_s, transports.llm)
        self._kb = client(settings.kb_url, settings.kb_timeout_s, transports.kb)
        self._analyst = client(settings.analyst_url, settings.kb_timeout_s, transports.analyst)
        self._agents: Agents | None = None

    def agents(self) -> Agents:
        if self._tools is None or self._kb is None:
            raise UpstreamError("TOOLS_URL and KB_URL must be configured")
        if self._agents is None:
            s = self._settings
            kb = KnowledgeBaseClient(self._kb)
            runner = AgentRunner(
                LlmClient(self._tools, model=s.llm_model),
                s.prompts_dir,
                max_tokens=s.llm_max_tokens,
                tracer=tracer_for(s.service_name, s),
            )
            analyst = AnalystClient(self._analyst) if self._analyst else None
            self._agents = Agents(
                IdeatorService(runner, kb, s),
                GapAnalystService(runner, kb, analyst, s),
                SkepticService(runner, kb, s),
                ChangeService(runner, kb, s),
                RewriterService(runner, s),
            )
        return self._agents

    async def prompts_ok(self) -> bool:
        try:
            for name in PROMPT_NAMES:
                load_prompt(self._settings.prompts_dir, name)
        except Exception:
            log.error("prompts_invalid", exc_info=True)
            return False
        return True

    @staticmethod
    async def _ping(http: ServiceClient | None) -> bool:
        if http is None:
            return False
        try:
            await http.request("GET", "/health")
        except DomainError:
            return False
        return True

    async def tools_up(self) -> bool:
        return await self._ping(self._tools)

    async def kb_up(self) -> bool:
        return await self._ping(self._kb)

    async def analyst_up(self) -> bool:
        return self._analyst is None or await self._ping(self._analyst)

    async def aclose(self) -> None:
        for http in (self._tools, self._kb, self._analyst):
            if http is not None:
                await http.aclose()
