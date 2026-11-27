"""LiteLLM adapter: the only place a provider SDK is touched (ADR-003)."""

import os

# Before the import: LiteLLM otherwise downloads its price list from GitHub at start-up, which is
# egress we did not allow-list, and the bundled list is pinned with the package version.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm.exceptions import Timeout as LiteLlmTimeout

from .llm import ProviderResult

litellm.telemetry = False


class LiteLlmProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        timeout_s: float,
    ) -> ProviderResult:
        kwargs: dict[str, object] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "api_key": self._api_key,
            "timeout": timeout_s,
            "num_retries": 0,  # retries belong to the caller, so each one is counted in the budget
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await litellm.acompletion(**kwargs)
        except LiteLlmTimeout as exc:
            raise TimeoutError from exc
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage
        try:
            cost: float | None = float(litellm.completion_cost(completion_response=response))
        except Exception:  # model missing from the price list: the service charges its fallback
            cost = None
        return ProviderResult(
            content=content,
            tokens_in=int(usage.prompt_tokens),
            tokens_out=int(usage.completion_tokens),
            cost_usd=cost,
            provider=model.split("/", 1)[0],
            model=str(getattr(response, "model", None) or model),
        )
