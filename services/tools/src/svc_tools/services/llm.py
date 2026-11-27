"""M5 ``/llm``: the only path from any agent to a hosted model (CLAUDE.md rule 4, ADR-003).

Order of every call: resolve the alias -> check the provider host is allow-listed -> check the run
budget **before** spending anything -> call the provider with a timeout -> charge the real cost
(or the alias fallback price when the provider cannot price it) -> write one ``llm_call`` trace.
Agents send an alias such as ``hosted-default``; a provider model name never comes from a caller.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field

from common.errors import (
    BudgetExceeded,
    Forbidden,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from common.schemas.base import Message
from common.tracing import Tracer

MAX_PLANNED_INPUT_CHARS_PER_TOKEN = 4  # rough planning estimate; the real count is charged after


class ChatMessage(Message):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=32_000)


class LlmRequest(Message):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    model: str = "hosted-default"
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    max_tokens: int = Field(ge=1, le=4096)
    temperature: float = Field(default=0, ge=0, le=1)
    response_format: Literal["json", "text"] = "text"


class LlmResponse(Message):
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    provider: str
    model: str


@dataclass(frozen=True)
class ProviderResult:
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: float | None  # None: the provider could not price this call
    provider: str
    model: str


@dataclass(frozen=True)
class ModelAlias:
    model: str
    host: str
    fallback_usd_per_1k_tokens: float


@dataclass(frozen=True)
class Spent:
    cost_usd: float
    tokens: int


class Provider(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        timeout_s: float,
    ) -> ProviderResult: ...


class LlmBudget(Protocol):
    def check(self, run_id: str, planned_tokens: int) -> None:
        """Raise ``BudgetExceeded`` if the run may not make another model call."""

    def charge(self, run_id: str, tokens: int, cost_usd: float) -> None: ...

    def spent(self, run_id: str) -> Spent: ...


class MemoryLlmBudget:
    """Per-run token and cost limits. The Redis-backed ledger replaces it in the cluster (P2-T13).

    shortcut: the cost of a call is only known afterwards, so ``check`` compares *already spent*
    cost with the limit. Concurrent calls can overshoot by up to (concurrency x one call's cost);
    M3 caps concurrency, and the next call is refused. Upgrade to reservations with the ledger.
    """

    def __init__(self, max_tokens: int, max_cost_usd: float) -> None:
        if max_tokens <= 0 or max_cost_usd <= 0:
            raise ValueError("budget limits must be positive")
        self._max_tokens, self._max_cost = max_tokens, max_cost_usd
        self._used: dict[str, tuple[float, int]] = {}

    def check(self, run_id: str, planned_tokens: int) -> None:
        cost, tokens = self._used.get(run_id, (0.0, 0))
        limits = {"max_cost_usd": self._max_cost, "max_tokens": self._max_tokens}
        if cost >= self._max_cost:
            raise BudgetExceeded("cost budget for this run is used up", limits)
        if tokens + planned_tokens > self._max_tokens:
            raise BudgetExceeded("token budget for this run would be exceeded", limits)

    def charge(self, run_id: str, tokens: int, cost_usd: float) -> None:
        cost, used = self._used.get(run_id, (0.0, 0))
        self._used[run_id] = (cost + cost_usd, used + tokens)

    def spent(self, run_id: str) -> Spent:
        cost, tokens = self._used.get(run_id, (0.0, 0))
        return Spent(cost_usd=cost, tokens=tokens)


class LlmService:
    def __init__(
        self,
        *,
        provider: Provider,
        budget: LlmBudget,
        aliases: dict[str, ModelAlias],
        allowed_hosts: frozenset[str],
        tracer: Tracer,
        timeout_s: float,
    ) -> None:
        self._provider = provider
        self._budget = budget
        self._aliases = aliases
        self._allowed_hosts = allowed_hosts
        self._tracer = tracer
        self._timeout_s = timeout_s

    def spent(self, run_id: str) -> Spent:
        return self._budget.spent(run_id)

    async def complete(self, req: LlmRequest) -> LlmResponse:
        alias = self._aliases.get(req.model)
        summary: dict[str, Any] = {
            "alias": req.model,
            "messages": len(req.messages),
            "output_cap": req.max_tokens,  # not "max_tokens": the redactor masks keys with "token"
            "response_format": req.response_format,
        }
        with self._tracer.span(
            req.agent,
            "llm_call",
            run_id=req.run_id,
            model=alias.model if alias else None,
            input=summary,
        ) as span:
            if alias is None:
                raise ValidationFailed("unknown model alias", {"model": req.model})
            if alias.host not in self._allowed_hosts:
                raise Forbidden(
                    "provider host is not allow-listed", {"reason": "PROVIDER_NOT_ALLOWED"}
                )
            planned = req.max_tokens + _estimate_input_tokens(req)
            self._budget.check(req.run_id, planned)

            started = time.perf_counter()
            result = await self._call(req, alias)
            latency_ms = int((time.perf_counter() - started) * 1000)

            if not result.content.strip():
                raise UpstreamError("the model returned an empty answer")
            tokens = result.tokens_in + result.tokens_out
            cost = (
                result.cost_usd
                if result.cost_usd is not None
                else tokens * alias.fallback_usd_per_1k_tokens / 1000
            )
            self._budget.charge(req.run_id, tokens, cost)
            span.set_usage(tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=cost)
            return LlmResponse(
                content=result.content,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
                provider=result.provider,
                model=result.model,
            )

    async def _call(self, req: LlmRequest, alias: ModelAlias) -> ProviderResult:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._provider.complete(
                    model=alias.model,
                    messages=[m.model_dump() for m in req.messages],
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    json_mode=req.response_format == "json",
                    timeout_s=self._timeout_s,
                )
        except TimeoutError as exc:
            raise UpstreamTimeout("the model provider did not answer in time") from exc
        except Exception as exc:  # never forward provider text: it can contain keys or prompts
            raise UpstreamError(
                "the model provider call failed", {"error_type": type(exc).__name__}
            ) from exc


def _estimate_input_tokens(req: LlmRequest) -> int:
    return sum(len(m.content) for m in req.messages) // MAX_PLANNED_INPUT_CHARS_PER_TOKEN + 1
