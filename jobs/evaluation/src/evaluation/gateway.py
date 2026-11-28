"""A small HTTP client for the gateway (and, for traces, M13) with bounded retries.

Retried (at most ``retries`` times, with a short back-off): timeouts, connection errors, 429 (the
``Retry-After`` header is honoured up to ``MAX_WAIT_S``) and 5xx. Any other 4xx fails at once. The
body of an error response is never put into a message: it can quote the idea text.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

MAX_WAIT_S = 30.0
BASE_BACKOFF_S = 0.5

Sleep = Callable[[float], Awaitable[None]]


class GatewayError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class NotFoundError(GatewayError):
    """404: the thing is not there (e.g. a trace that was never stored)."""


def _wait(response: httpx.Response | None, attempt: int) -> float:
    delay: float = BASE_BACKOFF_S * float(2**attempt)
    if response is not None:
        raw = response.headers.get("retry-after", "")
        if raw.strip().isdigit():
            delay = float(int(raw))
    return min(delay, MAX_WAIT_S)


class Gateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        retries: int = 2,
        sleep: Sleep = asyncio.sleep,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._retries = max(0, min(retries, 2))
        self._sleep = sleep
        self._headers = dict(default_headers or {})

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        sent = {**self._headers, **(headers or {})}
        if token:
            sent["Authorization"] = f"Bearer {token}"
        last = "request failed"
        for attempt in range(self._retries + 1):
            response: httpx.Response | None = None
            try:
                response = await self._client.request(method, path, json=body, headers=sent)
            except httpx.TimeoutException:
                last = "timed out"
            except httpx.TransportError:
                last = "connection failed"
            if response is not None:
                code = response.status_code
                if code == 429 or code >= 500:
                    last = f"HTTP {code}"
                elif code == 404:
                    raise NotFoundError(f"{method} {path}: HTTP 404", 404)
                elif code >= 400:
                    raise GatewayError(f"{method} {path}: HTTP {code}", code)
                else:
                    try:
                        return response.json()
                    except ValueError:
                        raise GatewayError(f"{method} {path}: malformed JSON", code) from None
            if attempt < self._retries:
                await self._sleep(_wait(response, attempt))
        raise GatewayError(
            f"{method} {path}: {last} after {self._retries + 1} tries", retryable=True
        )
