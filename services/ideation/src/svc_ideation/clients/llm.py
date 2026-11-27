"""M5 ``POST /llm/v1/chat/completions`` (the only route to a model; docs/harness/05 M5).

Assumed request: ``{run_id, agent, model, messages, max_tokens, temperature, response_format}``.
Assumed reply: ``{content, tokens_in, tokens_out, cost_usd, latency_ms, provider, model}``; only
``content`` is required, the rest default so a lean M5 still works.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from common.errors import UpstreamError
from common.http import ServiceClient

LLM_PATH = "/llm/v1/chat/completions"


class LlmReply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str | None = None


class LlmClient:
    def __init__(self, http: ServiceClient, *, model: str = "hosted-default") -> None:
        self._http = http
        self._model = model

    async def complete(
        self,
        *,
        run_id: str,
        agent: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> LlmReply:
        body: dict[str, Any] = {
            "run_id": run_id,
            "agent": agent,
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": "json",
        }
        payload = await self._http.post_json(LLM_PATH, body)
        try:
            return LlmReply.model_validate(payload)
        except ValidationError as exc:
            raise UpstreamError("M5 /llm returned a malformed reply") from exc
