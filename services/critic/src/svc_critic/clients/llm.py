"""Typed client for M5 ``POST /llm/v1/chat/completions`` (the critic's only route to a model)."""

from typing import Any

import httpx

from common.context import run_id_var
from common.errors import UpstreamError
from common.http import ServiceClient

LLM_PATH = "/llm/v1/chat/completions"


class LlmClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        timeout_s: float,
        internal_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        # no retries: one judge call per claim keeps the cost bound exact
        self._http = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=0,
            internal_token=internal_token,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        body = {
            "run_id": run_id_var.get() or "critic",
            "agent": "critic",
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
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise UpstreamError("M5 /llm returned an unexpected response") from exc
        if not isinstance(content, str):
            raise UpstreamError("M5 /llm returned an unexpected response")
        return content
