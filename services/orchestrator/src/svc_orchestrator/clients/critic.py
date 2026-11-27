"""M12 Critic and Guardrails client: claim checks and approval evaluation."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from common.schemas import ClaimCheckResponse

from .base import BaseClient


class ApprovalEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    needs_approval: bool
    spent: float | None = None
    budget: float | None = None


@dataclass(frozen=True)
class ObjectionFilter:
    accepted_quotes: dict[str, str]  # objection_id -> supporting quote
    rejected: dict[str, str]  # objection_id -> reason

    @property
    def accepted_ids(self) -> set[str]:
        return set(self.accepted_quotes)


MAX_OBJECTION_TEXT = 600  # the critic's limit for one objection


class CriticClient(BaseClient):
    name = "M12 critic"

    async def check(self, run_id: str, claims: list[dict[str, Any]]) -> ClaimCheckResponse:
        data = await self._call("POST", "/check", {"run_id": run_id, "claims": claims})
        return self._parse(ClaimCheckResponse, data)

    async def evaluate_approval(
        self, run_id: str, action: str, estimated_cost_usd: float
    ) -> ApprovalEvaluation:
        body = {"run_id": run_id, "action": action, "estimated_cost_usd": estimated_cost_usd}
        return self._parse(ApprovalEvaluation, await self._call("POST", "/approval/evaluate", body))

    async def filter_objections(
        self, run_id: str, objections: list[dict[str, Any]]
    ) -> ObjectionFilter:
        """Send M8 objections (``claim`` becomes ``text``); only evidence-backed ones return."""
        body = {
            "run_id": run_id,
            "objections": [
                {
                    "objection_id": o["objection_id"],
                    "dimension": o.get("dimension"),
                    "text": str(o["claim"])[:MAX_OBJECTION_TEXT],
                    "evidence_ids": list(o["evidence_ids"]),
                }
                for o in objections
            ],
        }
        data = self._object(await self._call("POST", "/objections/filter", body), "filter response")
        accepted, rejected = data.get("accepted"), data.get("rejected")
        if not isinstance(accepted, list) or not isinstance(rejected, list):
            raise self.bad("M12 critic returned a filter response without accepted/rejected lists")
        try:
            quotes = {str(a["objection_id"]): str(a.get("supporting_quote", "")) for a in accepted}
            reasons = {str(r["objection_id"]): str(r.get("reason", "")) for r in rejected}
        except (KeyError, TypeError) as exc:
            raise self.bad("M12 critic returned a malformed objection result") from exc
        return ObjectionFilter(quotes, reasons)
