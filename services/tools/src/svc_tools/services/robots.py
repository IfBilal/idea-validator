"""robots.txt handling (RFC 9309): cached 24 h; 4xx means no rules, 5xx or errors mean disallow."""

from typing import Any, Protocol
from urllib.robotparser import RobotFileParser

import httpx

from common.errors import DomainError

from .cache import KeyValueCache


class RobotsSource(Protocol):
    async def fetch_robots(self, host: str) -> tuple[int, str]:
        """Return ``(status, body)`` of ``https://host/robots.txt``."""


class RobotsChecker:
    def __init__(
        self,
        source: RobotsSource,
        cache: KeyValueCache,
        user_agent: str,
        exempt_hosts: set[str],
        ttl_s: int = 86_400,
    ) -> None:
        self._source = source
        self._cache = cache
        self._agent = user_agent.split("/", 1)[0].split()[0]
        self._exempt = exempt_hosts
        self._ttl = ttl_s

    async def allowed(self, host: str, path: str) -> bool:
        if host in self._exempt:  # official API hosts: governed by their API terms
            return True
        key = f"robots:{host}"
        cached = await self._cache.get(key)
        if cached is None:
            cached = await self._load(host)
            await self._cache.set(key, cached, self._ttl if cached["reachable"] else 300)
        if not cached["reachable"]:
            return False
        if not cached["body"].strip():
            return True
        parser = RobotFileParser()
        parser.parse(cached["body"].splitlines())
        return parser.can_fetch(self._agent, path)

    async def _load(self, host: str) -> dict[str, Any]:
        try:
            status, body = await self._source.fetch_robots(host)
        except (httpx.HTTPError, OSError, DomainError):
            return {"reachable": False, "body": ""}
        if status == 200:
            return {"reachable": True, "body": body}
        if 400 <= status < 500:  # no robots file or not available to us: no rules
            return {"reachable": True, "body": ""}
        return {"reachable": False, "body": ""}
