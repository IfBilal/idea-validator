"""Reads ``robots.txt`` through the same allow-list, SSRF guard and pinned fetcher."""

from .fetcher import Fetcher
from .url_policy import UrlPolicy

MAX_ROBOTS_BYTES = 500_000


class PolicyRobotsSource:
    def __init__(self, policy: UrlPolicy, fetcher: Fetcher, timeout_s: float = 10.0) -> None:
        self._policy, self._fetcher, self._timeout = policy, fetcher, timeout_s

    async def fetch_robots(self, host: str) -> tuple[int, str]:
        checked = await self._policy.check(f"https://{host}/robots.txt")
        raw = await self._fetcher.get(checked, self._timeout, MAX_ROBOTS_BYTES)
        return raw.status, raw.body.decode("utf-8", errors="replace")
