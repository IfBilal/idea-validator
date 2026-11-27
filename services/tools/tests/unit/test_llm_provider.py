"""LiteLLM adapter: maps its response and errors onto ProviderResult / TimeoutError (P2-T12)."""

from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from svc_tools.services.llm_provider import LiteLlmProvider


def litellm_response(content: str | None = "hello") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4),
        model="prov/small",
    )


class Capture:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.error: BaseException | None = None

    async def acompletion(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return litellm_response()


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Capture:
    cap = Capture()
    monkeypatch.setattr(litellm, "acompletion", cap.acompletion)
    monkeypatch.setattr(litellm, "completion_cost", lambda **_: 0.00042)
    return cap


async def test_a_response_is_mapped_with_usage_and_cost(capture: Capture) -> None:
    provider = LiteLlmProvider(api_key="k-secret")
    out = await provider.complete(
        model="prov/small",
        messages=[{"role": "user", "content": "x"}],
        max_tokens=50,
        temperature=0,
        json_mode=True,
        timeout_s=9,
    )
    assert (out.content, out.tokens_in, out.tokens_out, out.cost_usd) == ("hello", 11, 4, 0.00042)
    assert out.provider == "prov" and out.model == "prov/small"


async def test_call_settings_are_explicit_and_safe(capture: Capture) -> None:
    provider = LiteLlmProvider(api_key="k-secret")
    await provider.complete(
        model="prov/small",
        messages=[{"role": "user", "content": "x"}],
        max_tokens=50,
        temperature=0,
        json_mode=True,
        timeout_s=9,
    )
    kw = capture.kwargs
    assert kw["api_key"] == "k-secret" and kw["timeout"] == 9 and kw["max_tokens"] == 50
    assert kw["temperature"] == 0 and kw["response_format"] == {"type": "json_object"}
    assert kw["num_retries"] == 0, "retries are decided by the caller and counted in the budget"
    assert kw.get("stream") in (None, False)


async def test_text_mode_sends_no_response_format(capture: Capture) -> None:
    await LiteLlmProvider(api_key="k").complete(
        model="prov/small",
        messages=[{"role": "user", "content": "x"}],
        max_tokens=5,
        temperature=0,
        json_mode=False,
        timeout_s=1,
    )
    assert "response_format" not in capture.kwargs


async def test_an_unknown_price_is_reported_as_none_not_a_crash_and_not_zero(
    capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_price(**_: Any) -> float:
        raise ValueError("model not in cost map")

    monkeypatch.setattr(litellm, "completion_cost", no_price)
    out = await LiteLlmProvider(api_key="k").complete(
        model="prov/small",
        messages=[{"role": "user", "content": "x"}],
        max_tokens=5,
        temperature=0,
        json_mode=False,
        timeout_s=1,
    )
    assert out.cost_usd is None  # the service charges its fallback price: never "free"
