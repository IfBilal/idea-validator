"""Allow-list and SSRF guard for every outbound request M5 makes (docs/harness/13 §2).

A URL is accepted only if it is https on port 443, carries no credentials, names a host that is
in the allow-list **exactly**, and every address that host resolves to is a public one. IP
literals are never accepted. The caller connects to the checked addresses (see ``fetcher``), so a
DNS answer cannot change between the check and the connection.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from common.errors import Forbidden, UpstreamError

MAX_URL_LENGTH = 2048
_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f\\]")


class Resolver(Protocol):
    async def resolve(self, host: str) -> list[str]:
        """Return every IP address (as text) the host resolves to."""


class SystemResolver:
    async def resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        return sorted({str(info[4][0]) for info in infos})


@dataclass(frozen=True)
class CheckedUrl:
    host: str
    path_and_query: str
    addresses: tuple[str, ...]


def _deny(reason: str, message: str) -> Forbidden:
    return Forbidden(message, {"reason": reason})


def _normalise_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _is_public(address: str) -> bool:
    """True only for ordinary routable unicast addresses.

    ``is_global`` alone is not enough: it is True for multicast, so those are refused explicitly.
    IPv4-mapped and NAT64 IPv6 forms are judged by the IPv4 address they carry.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in ipaddress.ip_network("64:ff9b::/96"):
            ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
    return bool(
        ip.is_global
        and not (
            ip.is_multicast
            or ip.is_reserved
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_private
        )
    )


class UrlPolicy:
    def __init__(self, allow_list: Iterable[str], resolver: Resolver) -> None:
        self._allowed = frozenset(_normalise_host(h) for h in allow_list)
        self._resolver = resolver

    @property
    def allow_list(self) -> frozenset[str]:
        return self._allowed

    async def check(self, url: str) -> CheckedUrl:
        host, path_and_query = self._parse(url)
        if host not in self._allowed:
            raise _deny("DOMAIN_NOT_ALLOWED", "host is not on the allow-list")
        addresses = await self._resolver.resolve(host)
        if not addresses:
            raise UpstreamError("host did not resolve", {"host": host})
        if not all(_is_public(a) for a in addresses):
            raise _deny("BLOCKED_ADDRESS", "host resolves to a non-public address")
        return CheckedUrl(host, path_and_query, tuple(addresses))

    def _parse(self, url: str) -> tuple[str, str]:
        if not url or len(url) > MAX_URL_LENGTH or _FORBIDDEN_CHARS.search(url):
            raise _deny("INVALID_URL", "url is empty, too long or contains forbidden characters")
        parts = urlsplit(url)
        if parts.scheme.lower() != "https":
            raise _deny("INVALID_URL", "only https urls are allowed")
        try:
            port = parts.port
        except ValueError as exc:
            raise _deny("INVALID_URL", "invalid port") from exc
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise _deny("INVALID_URL", "credentials in the url are not allowed")
        if port not in (None, 443):
            raise _deny("INVALID_URL", "only port 443 is allowed")
        raw_host = parts.hostname or ""
        if not raw_host or "%" in raw_host:
            raise _deny("INVALID_URL", "missing or malformed host")
        host = _normalise_host(raw_host)
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            pass
        else:
            raise _deny("INVALID_URL", "ip addresses are never accepted")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise _deny("INVALID_URL", "host is not a valid domain name") from exc
        path = parts.path or "/"
        return host, path + (f"?{parts.query}" if parts.query else "")
