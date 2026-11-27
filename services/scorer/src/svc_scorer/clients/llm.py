"""Typed client for M5 ``POST /llm/v1/chat/completions``: the only way out to a hosted model."""

from typing import Any

import httpx

from common.errors import DomainError, UpstreamError
from common.http import ServiceClient

LLM_PATH = "/llm/v1/chat/completions"


class LlmClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        timeout_s: float,
        max_retries: int = 2,
        internal_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._http = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=max_retries,
            internal_token=internal_token,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, *, run_id: str, system: str, user: str, max_tokens: int) -> str:
        body = {
            "run_id": run_id,
            "agent": "scorer",
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": "json",
        }
        response = await self._http.request("POST", LLM_PATH, json=body)
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise UpstreamError("M5 /llm returned a non-JSON body") from exc
        content = _content(payload)
        if content is None:
            raise UpstreamError("M5 /llm returned an unexpected response shape")
        return content

    async def ping(self) -> bool:
        try:
            await self._http.request("GET", "/health")
        except DomainError:
            return False
        return True


def _content(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
    else:
        content = payload.get("content")
    return content if isinstance(content, str) else None
