"""M5 /llm service: budget gate before the call, cost charged and traced after it (P2-T12)."""

import asyncio
from typing import Any

import pytest

from common.errors import (
    BudgetExceeded,
    Forbidden,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from common.schemas.trace import TraceRecord
from common.tracing import Tracer
from svc_tools.services.llm import (
    LlmRequest,
    LlmService,
    MemoryLlmBudget,
    ModelAlias,
    ProviderResult,
)

HOST = "api.provider.test"
ALIASES = {
    "hosted-default": ModelAlias(model="prov/small", host=HOST, fallback_usd_per_1k_tokens=0.01)
}


class FakeProvider:
    def __init__(
        self, *, cost: float | None = 0.01, tokens_in: int = 100, tokens_out: int = 50
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cost, self.tokens_in, self.tokens_out = cost, tokens_in, tokens_out
        self.content = '{"score": 3}'
        self.error: BaseException | None = None
        self.delay_s = 0.0

    async def complete(self, **kwargs: Any) -> ProviderResult:
        self.calls.append(kwargs)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return ProviderResult(
            content=self.content,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=self.cost,
            provider="prov",
            model=kwargs["model"],
        )


def request(run_id: str = "run_1", **changes: Any) -> LlmRequest:
    base: dict[str, Any] = {
        "run_id": run_id,
        "agent": "scorer",
        "model": "hosted-default",
        "messages": [{"role": "user", "content": "score this idea"}],
        "max_tokens": 200,
    }
    return LlmRequest.model_validate({**base, **changes})


def service(
    provider: FakeProvider,
    *,
    max_cost: float = 0.05,
    max_tokens: int = 60_000,
    timeout_s: float = 5.0,
    sink: list[TraceRecord] | None = None,
    allowed: frozenset[str] = frozenset({HOST}),
) -> LlmService:
    return LlmService(
        provider=provider,
        budget=MemoryLlmBudget(max_tokens=max_tokens, max_cost_usd=max_cost),
        aliases=ALIASES,
        allowed_hosts=allowed,
        tracer=Tracer("tools", sink.append if sink is not None else None),
        timeout_s=timeout_s,
    )


async def test_a_call_returns_content_usage_cost_and_the_resolved_model() -> None:
    provider = FakeProvider(cost=0.0123, tokens_in=111, tokens_out=22)
    out = await service(provider).complete(request())
    assert out.content == '{"score": 3}'
    assert (out.tokens_in, out.tokens_out, out.cost_usd) == (111, 22, 0.0123)
    assert out.provider == "prov" and out.model == "prov/small"
    assert out.latency_ms >= 0


async def test_the_provider_gets_the_alias_target_temperature_and_json_mode() -> None:
    provider = FakeProvider()
    await service(provider).complete(request(temperature=0, response_format="json"))
    call = provider.calls[0]
    assert (
        call["model"] == "prov/small"
    )  # the alias, never the caller's string, reaches the provider
    assert call["temperature"] == 0 and call["json_mode"] is True and call["max_tokens"] == 200
    assert call["messages"] == [{"role": "user", "content": "score this idea"}]
    await service(provider).complete(request(response_format="text"))
    assert provider.calls[1]["json_mode"] is False


async def test_spend_accumulates_and_the_call_that_finds_the_budget_used_up_is_402() -> None:
    provider = FakeProvider(cost=0.03)
    svc = service(provider, max_cost=0.05)
    await svc.complete(request())  # spent 0.03 < 0.05
    await svc.complete(request())  # allowed: 0.03 < 0.05 at the time of the check; spent 0.06
    with pytest.raises(BudgetExceeded) as info:
        await svc.complete(request())
    assert len(provider.calls) == 2, "the provider must not be called once the budget is gone"
    assert info.value.status_code == 402
    assert info.value.details["max_cost_usd"] == 0.05


async def test_the_token_budget_is_checked_before_the_call_using_the_requested_max_tokens() -> None:
    provider = FakeProvider(tokens_in=100, tokens_out=50)
    svc = service(provider, max_tokens=1_000)
    await svc.complete(request(max_tokens=500))  # plans ~500 + small input, uses 150
    with pytest.raises(BudgetExceeded):
        await svc.complete(request(max_tokens=900))  # 150 spent + 900 planned > 1000
    assert len(provider.calls) == 1


async def test_budgets_are_per_run() -> None:
    provider = FakeProvider(cost=0.06)
    svc = service(provider, max_cost=0.05)
    await svc.complete(request("run_a"))
    with pytest.raises(BudgetExceeded):
        await svc.complete(request("run_a"))
    await svc.complete(request("run_b"))  # a different run is untouched


async def test_an_unknown_model_alias_is_422_and_the_provider_is_not_called() -> None:
    provider = FakeProvider()
    with pytest.raises(ValidationFailed):
        await service(provider).complete(request(model="gpt-4-anything"))
    assert provider.calls == []


async def test_an_alias_whose_provider_host_is_not_allow_listed_is_refused() -> None:
    provider = FakeProvider()
    svc = service(provider, allowed=frozenset({"other.example"}))
    with pytest.raises(Forbidden) as info:
        await svc.complete(request())
    assert info.value.details["reason"] == "PROVIDER_NOT_ALLOWED"
    assert provider.calls == []


async def test_a_slow_provider_is_504_and_nothing_is_charged() -> None:
    provider = FakeProvider(cost=0.04)
    provider.delay_s = 0.5
    svc = service(provider, timeout_s=0.05, max_cost=0.05)
    with pytest.raises(UpstreamTimeout):
        await svc.complete(request())
    provider.delay_s = 0.0
    await svc.complete(request())  # budget untouched by the failed call: still allowed
    await svc.complete(request())  # spent 0.04 then 0.08, the check before this one passes


async def test_provider_errors_are_502_and_never_leak_secrets() -> None:
    provider = FakeProvider()
    provider.error = RuntimeError("401 bad key sk-abcdefghijklmnopqrstuvwx for api.provider.test")
    with pytest.raises(UpstreamError) as info:
        await service(provider).complete(request())
    assert "sk-abcdefghijklmnopqrstuvwx" not in str(info.value)
    assert "sk-abcdefghijklmnopqrstuvwx" not in repr(info.value.details)


async def test_an_empty_model_answer_is_an_upstream_error_not_a_blank_success() -> None:
    provider = FakeProvider()
    provider.content = "   "
    with pytest.raises(UpstreamError):
        await service(provider).complete(request())


async def test_every_call_writes_an_llm_call_trace_with_tokens_cost_and_model() -> None:
    sink: list[TraceRecord] = []
    await service(FakeProvider(cost=0.02, tokens_in=10, tokens_out=5), sink=sink).complete(
        request("run_t", agent="scorer")
    )
    (record,) = sink
    assert record.kind == "llm_call" and record.run_id == "run_t" and record.agent == "scorer"
    assert (record.tokens_in, record.tokens_out, record.cost_usd) == (10, 5, 0.02)
    assert record.model == "prov/small" and record.error is None


async def test_a_failed_call_is_traced_with_its_error() -> None:
    sink: list[TraceRecord] = []
    provider = FakeProvider()
    provider.error = RuntimeError("boom")
    with pytest.raises(UpstreamError):
        await service(provider, sink=sink).complete(request())
    assert sink[0].kind == "llm_call" and sink[0].error is not None


async def test_a_budget_refusal_is_traced_as_an_error_too() -> None:
    sink: list[TraceRecord] = []
    svc = service(FakeProvider(cost=0.06), max_cost=0.05, sink=sink)
    await svc.complete(request())
    with pytest.raises(BudgetExceeded):
        await svc.complete(request())
    assert len(sink) == 2 and sink[1].error is not None


async def test_concurrent_calls_on_one_run_all_get_charged() -> None:
    provider = FakeProvider(cost=0.001)
    svc = service(provider, max_cost=10)
    await asyncio.gather(*(svc.complete(request()) for _ in range(20)))
    assert svc.spent("run_1").cost_usd == pytest.approx(0.02)
    assert svc.spent("run_1").tokens == 20 * 150


def test_the_budget_ledger_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="positive"):
        MemoryLlmBudget(max_tokens=0, max_cost_usd=1)
    with pytest.raises(ValueError, match="positive"):
        MemoryLlmBudget(max_tokens=1, max_cost_usd=0)


async def test_an_unknown_price_is_charged_at_the_configured_fallback_never_as_free() -> None:
    provider = FakeProvider(cost=None, tokens_in=100, tokens_out=50)  # the provider cannot price it
    sink: list[TraceRecord] = []
    svc = service(provider, sink=sink)
    out = await svc.complete(request())
    assert out.cost_usd == pytest.approx(0.0015)  # 150 tokens * 0.01 / 1000
    assert svc.spent("run_1").cost_usd == pytest.approx(0.0015)
    assert sink[0].cost_usd == pytest.approx(0.0015)


async def test_a_provider_reported_cost_wins_over_the_fallback() -> None:
    svc = service(FakeProvider(cost=0.0002, tokens_in=100, tokens_out=50))
    assert (await svc.complete(request())).cost_usd == 0.0002


async def test_an_unpriced_model_cannot_dodge_the_cost_limit() -> None:
    provider = FakeProvider(cost=None, tokens_in=3000, tokens_out=2000)  # 5000 tok = $0.05
    svc = service(provider, max_cost=0.05)
    await svc.complete(request())
    with pytest.raises(BudgetExceeded):
        await svc.complete(request())
