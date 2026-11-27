"""M12 Critic and Guardrails client: claim checks and approval evaluation."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from common.schemas import ClaimCheckResponse

from .base import BaseClient


class ApprovalEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    needs_approval: bool
    spent: float | None = None
    budget: float | None = None


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
