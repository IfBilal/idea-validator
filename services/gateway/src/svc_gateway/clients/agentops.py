"""Typed client for M13 (agentops): one run's trace. Nothing else of M13 is reachable from here.

The gateway only asks for ``GET /runs/{id}/trace`` and only after M2 confirmed the caller owns the
run. M13's listing (``GET /runs``) holds every user's runs and is never proxied.
"""

from typing import Any

from common.errors import UpstreamError
from common.http import ServiceClient

from .upstream import UNEXPECTED, guarded

DEFAULT_MAX_BYTES = 1024 * 1024


class AgentOpsClient:
    def __init__(self, client: ServiceClient, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def trace(self, run_id: str, flat: bool) -> dict[str, Any]:
        params = {"flat": "true"} if flat else None

        async def fetch() -> dict[str, Any]:
            response = await self._client.request("GET", f"/runs/{run_id}/trace", params=params)
            if len(response.content) > self._max_bytes:
                raise UpstreamError(UNEXPECTED)  # the browser never gets a megabyte-class answer
            return {"body": response.json()}

        data = (await guarded(fetch, "trace"))["body"]
        # a mixed-up answer for another run must never be forwarded
        if not isinstance(data, dict) or data.get("run_id") != run_id:
            raise UpstreamError(UNEXPECTED)
        return data
