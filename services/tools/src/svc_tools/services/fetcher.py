"""HTTP GET that connects to the addresses the URL policy already checked (no DNS rebinding).

The request goes to the IP, with the original ``Host`` header and TLS SNI/verification against the
original host name. Redirects are returned, never followed, so the service can re-check each hop.
Cookies, credentials and proxies are never used.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx

from common.errors import UpstreamError, UpstreamTimeout

from .url_policy import CheckedUrl


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool


class Fetcher(Protocol):
    async def get(self, checked: CheckedUrl, timeout_s: float, max_bytes: int) -> RawResponse: ...


async def _read(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` of the raw (never decoded) body."""
    body = bytearray()
    try:
        async for chunk in response.aiter_raw():
            body += chunk
            if len(body) > max_bytes:
                return bytes(body[:max_bytes]), True
    except httpx.StreamConsumed:  # in-memory test transports hand over the full content
        data = response.content
        return data[:max_bytes], len(data) > max_bytes
    return bytes(body), False


class PinnedHttpxFetcher:
    def __init__(
        self,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        total_deadline_s: float = 20.0,
    ) -> None:
        self._user_agent = user_agent
        self._transport = transport
        self._deadline = total_deadline_s

    async def get(self, checked: CheckedUrl, timeout_s: float, max_bytes: int) -> RawResponse:
        """GET with a total deadline (a slow-drip server cannot hold the connection open)."""
        try:
            async with asyncio.timeout(self._deadline):
                return await self._get(checked, timeout_s, max_bytes)
        except TimeoutError as exc:
            raise UpstreamTimeout(
                "upstream did not answer in time", {"host": checked.host}
            ) from exc

    async def post(
        self,
        checked: CheckedUrl,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> RawResponse:
        """POST JSON to the checked address (the LLM proxy). Same pinning, limits and deadline."""
        try:
            async with asyncio.timeout(self._deadline):
                return await self._send("POST", checked, timeout_s, max_bytes, headers, body)
        except TimeoutError as exc:
            raise UpstreamTimeout(
                "upstream did not answer in time", {"host": checked.host}
            ) from exc

    async def _get(self, checked: CheckedUrl, timeout_s: float, max_bytes: int) -> RawResponse:
        return await self._send("GET", checked, timeout_s, max_bytes, {}, None)

    async def _send(
        self,
        method: str,
        checked: CheckedUrl,
        timeout_s: float,
        max_bytes: int,
        extra_headers: dict[str, str],
        body_in: bytes | None,
    ) -> RawResponse:
        last: Exception | None = None
        for address in checked.addresses:
            literal = f"[{address}]" if ":" in address else address
            url = f"https://{literal}{checked.path_and_query}"
            headers = {
                "Host": checked.host,
                "User-Agent": self._user_agent,
                "Accept": "application/json, text/html;q=0.8, text/plain;q=0.7",
                "Accept-Encoding": "identity",
                **extra_headers,
            }
            try:
                async with (
                    httpx.AsyncClient(
                        transport=self._transport,
                        timeout=timeout_s,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client,
                    client.stream(
                        method,
                        url,
                        headers=headers,
                        content=body_in,
                        extensions={"sni_hostname": checked.host},
                    ) as response,
                ):
                    encoding = response.headers.get("content-encoding", "").strip().lower()
                    if encoding not in ("", "identity"):
                        # we asked for identity: a compressed answer could be a decompression bomb
                        raise UpstreamError("unsupported content encoding", {"host": checked.host})
                    body, truncated = await _read(response, max_bytes)
                    kept = {k.lower(): v for k, v in response.headers.items()}
                    return RawResponse(response.status_code, kept, body, truncated)
            except httpx.TimeoutException as exc:
                last = exc
                continue
            except httpx.HTTPError as exc:
                last = exc
                continue
        if isinstance(last, httpx.TimeoutException):
            raise UpstreamTimeout("upstream did not answer in time", {"host": checked.host})
        raise UpstreamError("upstream could not be reached", {"host": checked.host})
