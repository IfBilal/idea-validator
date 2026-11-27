"""M7 Analyst client.

Assumed ``GET /gaps?niche=`` -> ``{gaps:[{gap_id, text, evidence_ids[]}]}``.
"""

from pydantic import BaseModel, ConfigDict, ValidationError

from common.errors import UpstreamError
from common.http import ServiceClient


class AnalystGap(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    gap_id: str
    text: str = ""
    evidence_ids: list[str] = []


class AnalystClient:
    def __init__(self, http: ServiceClient) -> None:
        self._http = http

    async def get_gaps(self, niche: str) -> list[AnalystGap]:
        payload = await self._http.get_json("/gaps", params={"niche": niche})
        try:
            return [AnalystGap.model_validate(raw) for raw in payload["gaps"]]
        except (KeyError, TypeError, ValidationError) as exc:
            raise UpstreamError("M7 returned a malformed gaps payload") from exc
