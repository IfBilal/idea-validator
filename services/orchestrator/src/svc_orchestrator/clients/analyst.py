"""M7 Niche and Trend Analyst client: features, niche matching and gaps."""

from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, ValidationError

from common.schemas.types import EvidenceId

from .base import BaseClient


class NicheMatch(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    niche_id: str
    name: str
    status: str | None = None

    def as_m8(self) -> dict[str, Any]:
        """The ``niches[]`` shape M8 ``/ideate`` takes."""
        return {"name": self.name, "status": self.status}


class _Gap(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    niche_id: str | None = None
    description: str
    evidence_ids: list[EvidenceId]


class AnalystClient(BaseClient):
    name = "M7 analyst"

    async def features(
        self, idea_text: str, evidence_ids: list[str], profile: dict[str, Any]
    ) -> dict[str, Any]:
        body = {"idea_text": idea_text, "evidence_ids": evidence_ids, "profile": profile}
        return self._object(await self._call("POST", "/features", body), "features response")

    async def match_niches(
        self, profile: dict[str, Any], niche_wish: str | None, *, limit: int = 5
    ) -> list[NicheMatch]:
        body = {"profile": profile, "niche_wish": niche_wish, "limit": limit}
        data = self._object(await self._call("POST", "/niches/match", body), "niches response")
        matches = data.get("matches")
        if not isinstance(matches, list):
            raise self.bad("M7 analyst returned a niche match without a matches list")
        out: list[NicheMatch] = []
        for m in matches:
            try:
                out.append(NicheMatch.model_validate(m))
            except ValidationError:
                continue
        return out

    async def gaps(self, niche_id: str) -> list[dict[str, Any]]:
        """Gaps in the shape M8 ``/ideate`` takes: ``{gap_id, text, niche, evidence_ids}``."""
        data = self._object(
            await self._call("GET", f"/gaps?niche={quote(niche_id, safe='')}"), "gaps response"
        )
        raw = data.get("gaps")
        if not isinstance(raw, list):
            raise self.bad("M7 analyst returned a gaps response without a gaps list")
        out: list[dict[str, Any]] = []
        for g in raw:
            try:
                gap = _Gap.model_validate(g)
            except ValidationError:
                continue
            out.append(
                {
                    "gap_id": gap.id,
                    "text": gap.description,
                    "niche": gap.niche_id,
                    "evidence_ids": list(gap.evidence_ids),
                }
            )
        return out
