"""Hosted-LLM proxy (ADR-003): the only way any module reaches a model provider.

The provider host comes from ``config/llm.yaml`` and must be on the fetch allow-list. Calls go
through the same URL policy (SSRF guard, pinned address) as every fetch. The API key lives only
in this process's environment and is sent only to that provider. Prompts, completions and keys
are never logged. Spend is capped per run: a pre-flight check refuses calls that cannot fit, the
actual cost is charged afterwards, and a run that went over is stopped.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any, Protocol

from common.errors import (
    BudgetExceeded,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from common.logging import get_logger
from common.tracing import Tracer

from ..config_llm import Alias, LlmConfig
from ..models.llm import LlmRequest, LlmResponse
from .fetcher import RawResponse
from .url_policy import CheckedUrl, UrlPolicy

MAX_RETRIES = 2
BACKOFF_S = 0.5
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_REPLY_BYTES = 2_000_000
MAX_COUNT = 10_000_000
MILLION = Decimal(1_000_000)
log = get_logger("tools.llm")


class Poster(Protocol):
    async def post(
        self,
        checked: CheckedUrl,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> RawResponse: ...


class LlmMetrics(Protocol):
    def usage(self, alias: str, tokens_in: int, tokens_out: int, cost_usd: float) -> None: ...


class LlmBudget:
    """Per-run token and cost caps (the orchestrator enforces the exact per-kind budgets)."""

    def __init__(self, max_tokens: int, max_cost_usd: Decimal) -> None:
        self._max_tokens, self._max_cost = max_tokens, max_cost_usd
        self._used: dict[str, tuple[int, Decimal]] = {}

    def preflight(self, run_id: str, worst_case_cost: Decimal) -> None:
        tokens, cost = self._used.get(run_id, (0, Decimal(0)))
        if tokens >= self._max_tokens or cost + worst_case_cost > self._max_cost:
            raise BudgetExceeded(
                "the LLM budget of this run does not allow another call",
                {"max_tokens": self._max_tokens, "max_cost_usd": float(self._max_cost)},
            )

    def charge(self, run_id: str, tokens: int, cost: Decimal) -> None:
        used_tokens, used_cost = self._used.get(run_id, (0, Decimal(0)))
        total_tokens, total_cost = used_tokens + tokens, used_cost + cost
        self._used[run_id] = (total_tokens, total_cost)
        if total_tokens > self._max_tokens or total_cost > self._max_cost:
            raise BudgetExceeded(
                "the LLM budget of this run is used up",
                {"max_tokens": self._max_tokens, "max_cost_usd": float(self._max_cost)},
            )


def _estimate(text_bytes: int) -> int:
    """Upper bound on tokens: one per UTF-8 byte (CJK/emoji approach it; English is ~4x less).

    It only feeds the pre-flight refusal and the fallback when a provider reports no usage, so
    overestimating fails closed; the exact usage the provider reports is what gets charged.
    """
    return max(1, text_bytes)


def _count(value: Any, fallback: int) -> int:
    ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_COUNT
    return int(value) if ok else fallback


def _cost(alias: Alias, tokens_in: int, tokens_out: int) -> Decimal:
    price_in, price_out = (
        Decimal(str(alias.price_in_per_mtok)),
        Decimal(str(alias.price_out_per_mtok)),
    )
    return (tokens_in * price_in + tokens_out * price_out) / MILLION


class LlmService:
    def __init__(
        self,
        *,
        config: LlmConfig,
        policy: UrlPolicy,
        poster: Poster,
        keys: Mapping[str, str],
        budget: LlmBudget,
        sleep: Callable[[float], Awaitable[None]],
        clock: Callable[[], float],
        metrics: LlmMetrics | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._config, self._policy, self._poster, self._keys = config, policy, poster, keys
        self._budget, self._sleep, self._clock, self._metrics = budget, sleep, clock, metrics
        self._tracer = tracer

    async def complete(self, req: LlmRequest) -> LlmResponse:
        if self._tracer is None:
            return await self._complete(req)
        # the record keeps sizes and cost only: prompts and completions never go into a trace
        summary = {"alias": req.model, "messages": len(req.messages), "output_cap": req.max_tokens}
        with self._tracer.span(req.agent, "llm_call", run_id=req.run_id, input=summary) as span:
            out = await self._complete(req)
            span.model = out.model
            span.set_usage(
                tokens_in=out.tokens_in, tokens_out=out.tokens_out, cost_usd=float(out.cost_usd)
            )
            return out

    async def _complete(self, req: LlmRequest) -> LlmResponse:
        alias = self._config.aliases.get(req.model)
        if alias is None:
            raise ValidationFailed("unknown model alias", {"alias": req.model})
        key = self._keys.get(alias.api_key_env)
        if not key:
            raise UpstreamError("model alias is not configured", {"alias": req.model})

        est_in = _estimate(sum(len(m.content.encode()) for m in req.messages))
        self._budget.preflight(req.run_id, _cost(alias, est_in, req.max_tokens))

        checked = await self._policy.check(f"{alias.base_url.rstrip('/')}/chat/completions")
        body = json.dumps(self._body(alias, req)).encode()
        headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
        started = self._clock()
        raw = await self._send_with_retries(checked, headers, body, alias)
        latency_ms = int((self._clock() - started) * 1000)

        content, usage = self._parse(raw)
        tokens_in = _count(usage.get("prompt_tokens"), est_in)
        tokens_out = _count(usage.get("completion_tokens"), _estimate(len(content.encode())))
        cost = _cost(alias, tokens_in, tokens_out)
        log.info(
            "llm_call", alias=req.model, agent=req.agent, run_id=req.run_id,
            tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=float(cost), latency_ms=latency_ms,
        )  # fmt: skip
        if self._metrics:
            self._metrics.usage(req.model, tokens_in, tokens_out, float(cost))
        self._budget.charge(req.run_id, tokens_in + tokens_out, cost)
        return LlmResponse(
            content=content, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
            latency_ms=latency_ms, provider=alias.provider, model=alias.model,
        )  # fmt: skip

    @staticmethod
    def _body(alias: Alias, req: LlmRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": alias.model,
            "messages": [m.model_dump() for m in req.messages],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.response_format == "json":
            body["response_format"] = {"type": "json_object"}
        return body

    async def _send_with_retries(
        self, checked: CheckedUrl, headers: dict[str, str], body: bytes, alias: Alias
    ) -> RawResponse:
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            try:
                raw = await self._poster.post(
                    checked, headers, body, alias.timeout_s, MAX_REPLY_BYTES
                )
            except (UpstreamTimeout, UpstreamError):
                if last:
                    raise
                await self._sleep(BACKOFF_S * 2**attempt)
                continue
            if raw.status in RETRY_STATUSES:
                if last:
                    raise UpstreamError("the model provider keeps failing", {"status": raw.status})
                await self._sleep(BACKOFF_S * 2**attempt)
                continue
            if raw.status == 429:
                after = raw.headers.get("retry-after", "")
                raise RateLimited(
                    "the model provider is rate limiting us",
                    {"retry_after": int(after) if after.isdigit() else 30},
                )
            if not 200 <= raw.status < 300:
                # never echo the provider's body: it can contain key fragments or the prompt
                raise UpstreamError(
                    "the model provider rejected the request", {"status": raw.status}
                )
            return raw
        raise UpstreamError("the model provider could not be reached")  # pragma: no cover

    @staticmethod
    def _parse(raw: RawResponse) -> tuple[str, dict[str, Any]]:
        try:
            payload = json.loads(raw.body)
        except ValueError as exc:
            raise UpstreamError("the model provider returned invalid JSON") from exc
        choices = payload.get("choices") if isinstance(payload, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise UpstreamError("the model provider returned an unexpected response")
        usage = payload.get("usage")
        return content, usage if isinstance(usage, dict) else {}
