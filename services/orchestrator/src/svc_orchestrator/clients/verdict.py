"""M11 Verdict Engine client. The orchestrator only forwards what M11 returns."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from common.schemas import Verdict

from .base import BaseClient


class VerdictConfigView(BaseModel):
    """The versions M11 is running with (they go into every cache key)."""

    model_config = ConfigDict(frozen=True)

    engine: str
    rubrics: str
    weights: str
    rubric_versions: dict[str, str]


class VoiPoint(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    value: float
    verdict: str
    stability: str | None = None
    score: float | None = None


class VoiResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    low: VoiPoint
    high: VoiPoint
    verdict_changes: bool


class VerdictClient(BaseClient):
    name = "M11 verdict"

    async def config(self) -> VerdictConfigView:
        data = self._object(await self._call("GET", "/config"), "config response")
        versions = data.get("versions") or {}
        rules = data.get("rules") or {}
        return self._parse(
            VerdictConfigView,
            {
                "engine": versions.get("engine"),
                "rubrics": versions.get("rubrics"),
                "weights": versions.get("profile_weights"),
                "rubric_versions": rules.get("rubric_versions", {}),
            },
        )

    async def verdict(self, request: dict[str, Any]) -> Verdict:
        return self._parse(Verdict, await self._call("POST", "/verdict", request))

    async def voi(self, request: dict[str, Any]) -> VoiResult:
        return self._parse(VoiResult, await self._call("POST", "/value-of-information", request))
