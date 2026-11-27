"""HTTP GET that connects to the addresses the URL policy already checked (no DNS rebinding).

The request goes to the IP, with the original ``Host`` header and TLS SNI/verification against the
original host name. Redirects are returned, never followed, so the service can re-check each hop.
Cookies, credentials and proxies are never used.
"""

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


class PinnedHttpxFetcher:
    def __init__(self, user_agent: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._user_agent = user_agent
        self._transport = transport

    async def get(self, checked: CheckedUrl, timeout_s: float, max_bytes: int) -> RawResponse:
        last: Exception | None = None
        for address in checked.addresses:
            literal = f"[{address}]" if ":" in address else address
            url = f"https://{literal}{checked.path_and_query}"
            headers = {
                "Host": checked.host,
                "User-Agent": self._user_agent,
                "Accept": "application/json, text/html;q=0.8, text/plain;q=0.7",
                "Accept-Encoding": "identity",
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
                        "GET", url, headers=headers, extensions={"sni_hostname": checked.host}
                    ) as response,
                ):
                    body = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            truncated = True
                            del body[max_bytes:]
                            break
                    kept = {k.lower(): v for k, v in response.headers.items()}
                    return RawResponse(response.status_code, kept, bytes(body), truncated)
            except httpx.TimeoutException as exc:
                last = exc
                continue
            except httpx.HTTPError as exc:
                last = exc
                continue
        if isinstance(last, httpx.TimeoutException):
            raise UpstreamTimeout("upstream did not answer in time", {"host": checked.host})
        raise UpstreamError("upstream could not be reached", {"host": checked.host})
