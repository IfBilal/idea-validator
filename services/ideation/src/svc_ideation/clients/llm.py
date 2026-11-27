"""M5 ``POST /llm/v1/chat/completions`` (the only route to a model; docs/harness/05 M5).

Assumed request: ``{run_id, agent, model, messages, max_tokens, temperature, response_format}``.
Assumed reply: OpenAI-style ``choices[0].message.content`` (+ ``usage``) or flat
``{content, tokens_in, tokens_out, cost_usd, latency_ms, provider, model}``; only
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
            return LlmReply.model_validate(_normalise(payload))
        except ValidationError as exc:
            raise UpstreamError("M5 /llm returned a malformed reply") from exc


def _normalise(payload: Any) -> Any:
    """Accept M5's flat reply and the OpenAI ``choices``/``usage`` shape; else pass through."""
    if not isinstance(payload, dict) or "choices" not in payload:
        return payload
    choices = payload["choices"]
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    flat = {k: v for k, v in payload.items() if k not in ("choices", "usage")}
    flat["content"] = message.get("content") if isinstance(message, dict) else None
    usage = payload.get("usage")
    if isinstance(usage, dict):
        flat.setdefault("tokens_in", usage.get("prompt_tokens", 0))
        flat.setdefault("tokens_out", usage.get("completion_tokens", 0))
    return flat
