"""A small HTTP client for the gateway (and, for traces, M13) with bounded retries.

Retried (at most ``retries`` times, with a short back-off): timeouts, connection errors, 429 (the
``Retry-After`` header is honoured up to ``MAX_WAIT_S``) and 5xx. Any other 4xx fails at once. The
body of an error response is never put into a message: it can quote the idea text.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

MAX_WAIT_S = 30.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # a run view; traces ask for more explicitly
MAX_TRACE_BYTES = 8 * 1024 * 1024
BASE_BACKOFF_S = 0.5

Sleep = Callable[[float], Awaitable[None]]


class GatewayError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class NotFoundError(GatewayError):
    """404: the thing is not there (e.g. a trace that was never stored)."""


def _wait_from(headers: httpx.Headers | None, attempt: int) -> float:
    delay: float = BASE_BACKOFF_S * float(2**attempt)
    if headers is not None:
        raw = headers.get("retry-after", "").strip()
        if raw.isascii() and raw.isdigit():
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
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> Any:
        """One JSON call. The body is read as a stream and abandoned above ``max_bytes``."""
        sent = {**self._headers, **(headers or {})}
        if token:
            sent["Authorization"] = f"Bearer {token}"
        last = "request failed"
        for attempt in range(self._retries + 1):
            status: int | None = None
            retry_after: httpx.Headers | None = None
            try:
                async with self._client.stream(method, path, json=body, headers=sent) as response:
                    status, retry_after = response.status_code, response.headers
                    if status == 429 or status >= 500:
                        last = f"HTTP {status}"
                    elif status == 404:
                        raise NotFoundError(f"{method} {path}: HTTP 404", 404)
                    elif status >= 400:
                        raise GatewayError(f"{method} {path}: HTTP {status}", status)
                    else:
                        return await _read_json(response, method, path, max_bytes)
            except httpx.TimeoutException:
                last = "timed out"
            except httpx.TransportError:
                last = "connection failed"
            except httpx.RequestError:  # bad encoding, redirect loops: retrying cannot help
                raise GatewayError(f"{method} {path}: unusable response") from None
            if attempt < self._retries:
                await self._sleep(_wait_from(retry_after, attempt))
        raise GatewayError(
            f"{method} {path}: {last} after {self._retries + 1} tries", retryable=True
        )


async def _read_json(response: httpx.Response, method: str, path: str, cap: int) -> Any:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise GatewayError(f"{method} {path}: response too large", response.status_code)
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > cap:
            raise GatewayError(f"{method} {path}: response too large", response.status_code)
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError:  # includes bad UTF-8 and JSON errors
        raise GatewayError(f"{method} {path}: malformed JSON", response.status_code) from None
