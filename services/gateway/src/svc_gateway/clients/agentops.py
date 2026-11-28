"""Typed client for M13 (agentops): one run's trace. Nothing else of M13 is reachable from here.

The gateway only asks for ``GET /runs/{id}/trace`` and only after M2 confirmed the caller owns the
run. M13's listing (``GET /runs``) holds every user's runs and is never proxied.
"""

from typing import Any

from common.errors import UpstreamError
from common.http import ServiceClient

from ..models.trace import TraceView
from .upstream import UNEXPECTED, guarded, parse

DEFAULT_MAX_BYTES = 1024 * 1024


class AgentOpsClient:
    def __init__(self, client: ServiceClient, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def trace(self, run_id: str, flat: bool) -> dict[str, Any]:
        params = {"flat": "true"} if flat else None
        path = f"/runs/{run_id}/trace"
        data = await guarded(
            lambda: self._client.get_json_capped(path, max_bytes=self._max_bytes, params=params),
            "trace",
        )
        # a mixed-up answer for another run must never be forwarded
        if not isinstance(data, dict) or data.get("run_id") != run_id:
            raise UpstreamError(UNEXPECTED)
        return parse(TraceView, data).model_dump(exclude_none=True)
