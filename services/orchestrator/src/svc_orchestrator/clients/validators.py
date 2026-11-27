"""M9 Validators client: the four scorers run concurrently behind ``POST /score-all``."""

from dataclasses import dataclass
from typing import Any

from common.errors import UpstreamError

from .base import BaseClient


@dataclass(frozen=True)
class ScoreAllResult:
    scores: list[Any]  # raw: validated one by one so a single bad item abstains on its own
    extra: dict[str, Any]


class ValidatorsClient(BaseClient):
    name = "M9 validators"

    async def score_all(
        self,
        *,
        run_id: str,
        idea: str,
        profile: dict[str, Any],
        evidence: list[dict[str, Any]],
        features: dict[str, Any],
    ) -> ScoreAllResult:
        body = {
            "run_id": run_id,
            "idea": idea,
            "profile": profile,
            "evidence": evidence,
            "features": features,
        }
        data = self._object(await self._call("POST", "/score-all", body), "score-all response")
        scores = data.get("scores")
        if not isinstance(scores, list):
            raise UpstreamError("M9 validators returned a score-all response without scores")
        extra = data.get("extra")
        return ScoreAllResult(scores, extra if isinstance(extra, dict) else {})
