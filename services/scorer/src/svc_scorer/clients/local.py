"""Client for the llama.cpp server that holds the fine-tuned GGUF (``scorer-lora@v1``).

The server speaks the OpenAI chat format. Decoding is constrained by a JSON schema, so the reply
is always the four-key score object; the verdict is not even expressible. ``ready()`` is true only
after the model has loaded (``/health``) AND one warm-up completion has succeeded (11 §4).
"""

from typing import Any

import httpx

from common.errors import UpstreamError
from common.http import ServiceClient

from .llm import _content

CHAT_PATH = "/v1/chat/completions"
WARM_UP_MAX_TOKENS = 16

SCORE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"anyOf": [{"type": "number", "minimum": 0, "maximum": 5}, {"type": "null"}]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string", "maxLength": 600},
    },
    "required": ["score", "confidence", "evidence_ids", "reason"],
    "additionalProperties": False,
}


class LocalLlamaClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        # no transport-level retries: the scoring service owns the retry-once-then-fall-back policy
        self._http = ServiceClient(
            base_url, timeout_s=timeout_s, max_retries=0, transport=transport
        )
        self._warm = False

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, *, run_id: str, system: str, user: str, max_tokens: int) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "score", "strict": True, "schema": SCORE_JSON_SCHEMA},
            },
        }
        response = await self._http.request("POST", CHAT_PATH, json=body)
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise UpstreamError("llama server returned a non-JSON body") from exc
        content = _content(payload)
        if content is None:
            raise UpstreamError("llama server returned an unexpected response shape")
        return content

    async def ready(self) -> bool:
        """Model loaded and a warm-up completion has worked (remembered once it has)."""
        if self._warm:
            return True
        try:
            await self._http.request("GET", "/health")
            await self.complete(
                run_id="warm-up",
                system="Reply with JSON only.",
                user="warm-up",
                max_tokens=WARM_UP_MAX_TOKENS,
            )
        except Exception:
            return False
        self._warm = True
        return True
