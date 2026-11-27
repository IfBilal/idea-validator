"""Typed client for M10 ``POST /score``."""

import httpx

from common.errors import DomainError, UpstreamError
from common.http import ServiceClient
from common.schemas import DimensionScore, ScoreRequest


class ScorerClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float,
        max_retries: int = 2,
        internal_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = ServiceClient(
            base_url,
            timeout_s=timeout_s,
            max_retries=max_retries,
            internal_token=internal_token,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def score(self, request: ScoreRequest) -> DimensionScore:
        try:
            payload = await self._http.post_json("/score", request.model_dump(mode="json"))
            return DimensionScore.model_validate(payload)
        except ValueError as exc:  # bad JSON body or a body that is not a DimensionScore
            raise UpstreamError("M10 /score returned an invalid DimensionScore") from exc

    async def ping(self) -> bool:
        try:
            await self._http.request("GET", "/ready")
        except DomainError:
            return False
        return True
