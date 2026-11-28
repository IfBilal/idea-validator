import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from common.errors import (
    BudgetExceeded,
    Forbidden,
    RateLimited,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from common.schemas.trace import TraceRecord
from common.tracing import Tracer
from svc_tools.config_llm import LlmConfig, parse_llm_config
from svc_tools.models.llm import LlmRequest
from svc_tools.services.fetcher import RawResponse
from svc_tools.services.llm import LlmBudget, LlmService
from svc_tools.services.url_policy import CheckedUrl, UrlPolicy

PUBLIC = "104.18.0.1"
KEY = "gsk_live_SUPER_SECRET_KEY_123"
ALLOW = ["api.groq.com", "api.openai.com"]
CONFIG = parse_llm_config(
    {
        "aliases": {
            "hosted-default": {
                "provider": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "model": "llama-3.3-70b",
                "api_key_env": "GROQ_API_KEY",
                "price_in_per_mtok": 0.5,
                "price_out_per_mtok": 1.5,
            },
            "hosted-cheap": {
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "small",
                "api_key_env": "OPENAI_API_KEY",
                "price_in_per_mtok": 0.1,
                "price_out_per_mtok": 0.4,
            },
        }
    },
    ALLOW,
)


class Resolver:
    def __init__(self, address: str = PUBLIC) -> None:
        self.address = address

    async def resolve(self, host: str) -> list[str]:
        return [self.address]


class FakePoster:
    def __init__(self, *responses: RawResponse | Exception) -> None:
        self.script = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        checked: CheckedUrl,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> RawResponse:
        self.calls.append(
            {"host": checked.host, "path": checked.path_and_query, "headers": headers,
             "body": json.loads(body), "timeout": timeout_s}
        )  # fmt: skip
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def reply(
    content: Any = '{"score": 3}',
    usage: dict[str, Any] | None = None,
    status: int = 200,
    raw: bytes | None = None,
) -> RawResponse:
    body = raw
    if body is None:
        payload: dict[str, Any] = {
            "choices": [{"message": {"role": "assistant", "content": content}}]
        }
        payload["usage"] = (
            {"prompt_tokens": 1000, "completion_tokens": 500} if usage is None else usage
        )
        body = json.dumps(payload).encode()
    return RawResponse(status, {"content-type": "application/json"}, body, False)


def request(**changes: Any) -> LlmRequest:
    base: dict[str, Any] = {
        "run_id": "run_1",
        "agent": "scorer",
        "model": "hosted-default",
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
    }
    return LlmRequest.model_validate({**base, **changes})


def build(
    poster: FakePoster,
    keys: dict[str, str] | None = None,
    budget: LlmBudget | None = None,
    config: LlmConfig = CONFIG,
    resolver: Resolver | None = None,
    sleeps: list[float] | None = None,
    clock: list[float] | None = None,
    tracer: Tracer | None = None,
) -> LlmService:
    ticks = clock if clock is not None else [100.0, 100.25]
    sleeps = sleeps if sleeps is not None else []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def now() -> float:
        return ticks.pop(0) if len(ticks) > 1 else ticks[0]

    return LlmService(
        config=config,
        policy=UrlPolicy(ALLOW, resolver or Resolver()),
        poster=poster,
        keys={"GROQ_API_KEY": KEY, "OPENAI_API_KEY": "sk-other"} if keys is None else keys,
        budget=budget or LlmBudget(max_tokens=60_000, max_cost_usd=Decimal("0.25")),
        sleep=sleep,
        clock=now,
        tracer=tracer,
    )


# ---- the happy path
async def test_a_completion_returns_content_usage_cost_and_the_real_model() -> None:
    poster = FakePoster(reply('{"score": 4}'))
    out = await build(poster).complete(request())
    assert out.content == '{"score": 4}'
    assert (out.tokens_in, out.tokens_out) == (1000, 500)
    assert out.cost_usd == Decimal("0.00125")  # 1000*0.5/1e6 + 500*1.5/1e6
    assert (out.provider, out.model) == ("groq", "llama-3.3-70b")
    assert out.latency_ms == 250


async def test_the_provider_receives_the_key_a_pinned_path_and_an_openai_body() -> None:
    poster = FakePoster(reply())
    await build(poster).complete(request(max_tokens=300, temperature=0.2, response_format="json"))
    (call,) = poster.calls
    assert (call["host"], call["path"]) == ("api.groq.com", "/openai/v1/chat/completions")
    assert call["headers"]["authorization"] == f"Bearer {KEY}"
    assert call["body"] == {
        "model": "llama-3.3-70b",
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        "max_tokens": 300,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }


async def test_text_format_sends_no_response_format() -> None:
    poster = FakePoster(reply("hello"))
    await build(poster).complete(request(response_format="text"))
    assert "response_format" not in poster.calls[0]["body"]


async def test_aliases_route_to_their_own_provider_and_key() -> None:
    poster = FakePoster(reply())
    await build(poster).complete(request(model="hosted-cheap"))
    (call,) = poster.calls
    assert call["host"] == "api.openai.com"
    assert call["headers"]["authorization"] == "Bearer sk-other"


# ---- request validation (boundary values)
@pytest.mark.parametrize(
    ("value", "ok"), [(0, False), (1, True), (4096, True), (4097, False), (-1, False)]
)
def test_max_tokens_bounds(value: int, ok: bool) -> None:
    if ok:
        assert request(max_tokens=value).max_tokens == value
    else:
        with pytest.raises(ValidationError):
            request(max_tokens=value)


@pytest.mark.parametrize(("value", "ok"), [(0, True), (2, True), (-0.1, False), (2.1, False)])
def test_temperature_bounds(value: float, ok: bool) -> None:
    if ok:
        assert request(temperature=value).temperature == value
    else:
        with pytest.raises(ValidationError):
            request(temperature=value)


@pytest.mark.parametrize(
    "bad",
    [
        {"messages": []},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": "x" * 50_001}]},
        {"messages": [{"role": "user", "content": "x"}] * 51},
        {"run_id": ""},
        {"run_id": None},
        {"agent": ""},
        {"response_format": "xml"},
        {"stream": True},
        {"unknown": 1},
    ],
)
def test_invalid_requests_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        request(**bad)


def test_message_content_at_the_limit_is_accepted() -> None:
    assert request(messages=[{"role": "user", "content": "x" * 50_000}])


async def test_an_unknown_alias_is_a_validation_error() -> None:
    with pytest.raises(ValidationFailed, match="alias"):
        await build(FakePoster(reply())).complete(request(model="gpt-whatever"))


async def test_an_alias_without_a_configured_key_is_unavailable_not_a_crash() -> None:
    with pytest.raises(UpstreamError, match="not configured"):
        await build(FakePoster(reply()), keys={}).complete(request())


# ---- configuration is validated at load time
def alias(url: str) -> dict[str, Any]:
    return {
        "provider": "p", "base_url": url, "model": "m", "api_key_env": "KEY_NAME",
        "price_in_per_mtok": 0, "price_out_per_mtok": 0,
    }  # fmt: skip


def test_an_alias_pointing_outside_the_allow_list_refuses_to_load() -> None:
    bad = {"aliases": {"x": alias("https://evil.example/v1")}}
    with pytest.raises(ValueError, match="allow-list"):
        parse_llm_config(bad, ALLOW)


@pytest.mark.parametrize(
    "url",
    ["http://api.groq.com/v1", "https://api.groq.com:8443/v1", "https://user@api.groq.com/v1"],
)
def test_provider_urls_must_be_plain_https(url: str) -> None:
    bad = {"aliases": {"x": alias(url)}}
    with pytest.raises(ValueError):
        parse_llm_config(bad, ALLOW)


def test_prices_must_not_be_negative() -> None:
    bad = {"aliases": {"x": {**alias("https://api.groq.com/v1"), "price_in_per_mtok": -1}}}
    with pytest.raises(ValueError):
        parse_llm_config(bad, ALLOW)


def test_the_committed_config_loads_against_the_committed_allow_list() -> None:
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[4] / "config"
    sources = yaml.safe_load((root / "sources.yaml").read_text())
    config = parse_llm_config(
        yaml.safe_load((root / "llm.yaml").read_text()), sources["allow_list"]
    )
    assert {"hosted-default", "hosted-cheap"} <= set(config.aliases)


# ---- cost arithmetic
@pytest.mark.parametrize(
    ("usage", "cost"),
    [
        ({"prompt_tokens": 0, "completion_tokens": 0}, "0"),
        ({"prompt_tokens": 1, "completion_tokens": 0}, "0.0000005"),
        ({"prompt_tokens": 0, "completion_tokens": 1}, "0.0000015"),
        ({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, "2"),
    ],
)
async def test_cost_is_exact_decimal_arithmetic(usage: dict[str, int], cost: str) -> None:
    big = LlmBudget(max_tokens=100_000_000, max_cost_usd=Decimal(100))
    out = await build(FakePoster(reply(usage=usage)), budget=big).complete(request())
    assert out.cost_usd == Decimal(cost)


async def test_missing_or_garbage_usage_is_estimated_not_trusted() -> None:
    for usage in ({}, {"prompt_tokens": -5, "completion_tokens": "x"}, {"prompt_tokens": 1e12}):
        out = await build(FakePoster(reply("abcdefgh" * 10, usage=usage))).complete(request())
        assert out.tokens_in >= 1 and out.tokens_out >= 1
        assert out.tokens_in < 10_000_000
        assert out.cost_usd >= 0


# ---- budgets: per run, pre-flight and after the call
async def test_the_token_budget_is_enforced_per_run_before_calling_the_provider() -> None:
    poster = FakePoster(reply(usage={"prompt_tokens": 400, "completion_tokens": 100}))
    service = build(poster, budget=LlmBudget(max_tokens=1_000, max_cost_usd=Decimal("1")))
    await service.complete(request())  # 500 used
    await service.complete(request())  # 1000 used
    calls = len(poster.calls)
    with pytest.raises(BudgetExceeded):
        await service.complete(request())
    assert len(poster.calls) == calls  # refused before any network call


async def test_the_cost_budget_is_enforced() -> None:
    poster = FakePoster(reply(usage={"prompt_tokens": 1_000_000, "completion_tokens": 0}))
    service = build(poster, budget=LlmBudget(max_tokens=10_000_000, max_cost_usd=Decimal("0.6")))
    await service.complete(request())  # costs 0.5
    with pytest.raises(BudgetExceeded):
        await service.complete(request())


async def test_a_call_whose_worst_case_cost_would_cross_the_cap_is_refused_up_front() -> None:
    poster = FakePoster(reply())
    service = build(poster, budget=LlmBudget(max_tokens=100_000, max_cost_usd=Decimal("0.0000001")))
    with pytest.raises(BudgetExceeded):
        await service.complete(request(max_tokens=4096))
    assert poster.calls == []


async def test_budgets_are_independent_per_run() -> None:
    service = build(
        FakePoster(reply(usage={"prompt_tokens": 600, "completion_tokens": 0})),
        budget=LlmBudget(max_tokens=1_000, max_cost_usd=Decimal("1")),
    )
    await service.complete(request(run_id="a"))  # a: 600
    await service.complete(request(run_id="b"))  # b: 600, unaffected by a
    with pytest.raises(BudgetExceeded):
        await service.complete(request(run_id="a"))  # a: 1200 > 1000
    with pytest.raises(BudgetExceeded):
        await service.complete(request(run_id="b"))  # b: 1200 > 1000


# ---- retries and provider failures
async def test_transient_provider_errors_are_retried_twice_with_backoff() -> None:
    sleeps: list[float] = []
    poster = FakePoster(reply(status=503, raw=b"x"), reply(status=502, raw=b"x"), reply())
    out = await build(poster, sleeps=sleeps).complete(request())
    assert out.content == '{"score": 3}'
    assert len(poster.calls) == 3
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]


async def test_persistent_failure_is_an_upstream_error_after_three_attempts() -> None:
    poster = FakePoster(reply(status=500, raw=b"boom"))
    with pytest.raises(UpstreamError):
        await build(poster).complete(request())
    assert len(poster.calls) == 3


async def test_timeouts_are_retried_then_reported() -> None:
    poster = FakePoster(UpstreamTimeout("slow"))
    with pytest.raises(UpstreamTimeout):
        await build(poster).complete(request())
    assert len(poster.calls) == 3


async def test_provider_rate_limiting_is_passed_on_with_retry_after() -> None:
    limited = RawResponse(429, {"retry-after": "42"}, b"", False)
    poster = FakePoster(limited)
    sleeps: list[float] = []
    with pytest.raises(RateLimited) as caught:
        await build(poster, sleeps=sleeps).complete(request())
    assert caught.value.details["retry_after"] == 42
    assert len(poster.calls) == 1 and sleeps == [], "a long wait is the caller's decision, not ours"


async def test_a_short_provider_rate_limit_is_waited_out_and_the_call_succeeds() -> None:
    """Free-tier token-per-minute limits: six scorers in parallel get a 429 with retry-after of a few s."""
    limited = RawResponse(429, {"retry-after": "3"}, b"", False)
    poster = FakePoster(limited, reply())
    sleeps: list[float] = []
    out = await build(poster, sleeps=sleeps).complete(request())
    assert out.content and len(poster.calls) == 2
    assert sleeps == [3.0], "it waited exactly what the provider asked for"


async def test_a_rate_limit_that_keeps_coming_back_is_reported_after_three_waits() -> None:
    limited = RawResponse(429, {"retry-after": "2"}, b"", False)
    poster = FakePoster(limited)
    sleeps: list[float] = []
    with pytest.raises(RateLimited):
        await build(poster, sleeps=sleeps).complete(request())
    assert len(poster.calls) == 4 and sleeps == [2.0, 2.0, 2.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_provider_client_errors_are_a_generic_upstream_error_and_never_leak_details(
    status: int,
) -> None:
    detail = f"invalid api key {KEY}".encode()
    poster = FakePoster(reply(status=status, raw=detail))
    with pytest.raises(UpstreamError) as caught:
        await build(poster).complete(request())
    assert KEY not in str(caught.value) and KEY not in json.dumps(caught.value.details)
    assert len(poster.calls) == 1  # not retried


@pytest.mark.parametrize(
    "raw",
    [
        b"not json", b"[]", b"{}", b'{"choices": []}', b'{"choices": [{}]}',
        b'{"choices": [{"message": {}}]}', b'{"choices": [{"message": {"content": 5}}]}',
        b'{"choices": [{"message": {"content": null}}]}',
    ],
)  # fmt: skip
async def test_malformed_provider_replies_are_an_upstream_error(raw: bytes) -> None:
    with pytest.raises(UpstreamError):
        await build(FakePoster(reply(raw=raw))).complete(request())


async def test_an_empty_completion_is_returned_as_empty_text_not_an_error() -> None:
    out = await build(FakePoster(reply(""))).complete(request())
    assert out.content == ""


# ---- network safety
async def test_a_provider_name_resolving_to_a_private_address_is_blocked() -> None:
    poster = FakePoster(reply())
    with pytest.raises(Forbidden):
        await build(poster, resolver=Resolver("10.0.0.5")).complete(request())
    assert poster.calls == []


# ---- secrets never appear anywhere they should not
async def test_the_key_and_the_prompt_are_never_logged(capsys: pytest.CaptureFixture[str]) -> None:
    from common.logging import configure_logging

    configure_logging("tools", "DEBUG")
    secret_prompt = "my private business plan"
    await build(FakePoster(reply())).complete(
        request(messages=[{"role": "user", "content": secret_prompt}])
    )
    with pytest.raises(UpstreamError):
        await build(FakePoster(reply(status=401, raw=KEY.encode()))).complete(request())
    out = capsys.readouterr().out
    assert KEY not in out
    assert secret_prompt not in out


# ---- round 2: a conservative pre-flight and a real trace record
async def test_the_preflight_assumes_the_worst_tokenisation_of_the_input() -> None:
    # 40,000 CJK characters: "chars / 4" says 10k tokens ($0.005); a tokeniser can need one token
    # per byte (3 per character), i.e. 120k tokens = $0.06, which does not fit a $0.05 cap
    poster = FakePoster(reply())
    service = build(poster, budget=LlmBudget(max_tokens=10_000_000, max_cost_usd=Decimal("0.05")))
    cjk = request(messages=[{"role": "user", "content": "\u6f22" * 40_000}], max_tokens=1)
    with pytest.raises(BudgetExceeded):
        await service.complete(cjk)
    assert poster.calls == []


async def test_an_ordinary_english_prompt_still_fits_a_small_budget() -> None:
    poster = FakePoster(reply(usage={"prompt_tokens": 2000, "completion_tokens": 100}))
    service = build(poster, budget=LlmBudget(max_tokens=60_000, max_cost_usd=Decimal("0.05")))
    long_prompt = request(messages=[{"role": "user", "content": "word " * 1600}], max_tokens=800)
    assert (await service.complete(long_prompt)).tokens_in == 2000


async def test_every_call_writes_an_llm_call_trace_record_without_the_prompt() -> None:
    sink: list[TraceRecord] = []
    poster = FakePoster(reply('{"score": 4}', usage={"prompt_tokens": 30, "completion_tokens": 7}))
    service = build(poster, tracer=Tracer("tools", sink.append))
    secret_prompt = "PRIVATE-STUDENT-IDEA-TEXT"
    await service.complete(request(messages=[{"role": "user", "content": secret_prompt}]))
    (record,) = sink
    assert record.kind == "llm_call" and record.run_id == "run_1" and record.agent == "scorer"
    assert (record.tokens_in, record.tokens_out) == (30, 7)
    assert record.cost_usd == pytest.approx(30 * 0.5e-6 + 7 * 1.5e-6)
    assert record.model == "llama-3.3-70b" and record.error is None
    dumped = record.model_dump_json()
    assert secret_prompt not in dumped and '{"score": 4}' not in dumped


async def test_a_refused_or_failed_call_is_traced_with_its_error() -> None:
    sink: list[TraceRecord] = []
    poster = FakePoster(reply())
    service = build(
        poster,
        budget=LlmBudget(max_tokens=100_000, max_cost_usd=Decimal("0.0000001")),
        tracer=Tracer("tools", sink.append),
    )
    with pytest.raises(BudgetExceeded):
        await service.complete(request(max_tokens=4096))
    assert sink[0].kind == "llm_call" and sink[0].error is not None
