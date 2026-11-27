"""POST /approval/evaluate request and response."""

from typing import Literal

from pydantic import Field

from common.schemas import Message

RunKind = Literal["validate", "discover", "whatif", "challenge", "eval"]
RUN_KINDS: tuple[str, ...] = ("validate", "discover", "whatif", "challenge", "eval")
Decision = Literal["proceed", "needs_approval", "blocked"]


class ApprovalRequest(Message):
    run_id: str = Field(min_length=1, max_length=64)
    kind: RunKind
    action: str = Field(min_length=1, max_length=200)
    estimated_cost_usd: float = Field(ge=0, allow_inf_nan=False)
    spent_usd: float = Field(ge=0, allow_inf_nan=False)


class ApprovalDecision(Message):
    decision: Decision
    needs_approval: bool
    spent: float
    budget: float
    approval_threshold: float
    projected: float
    reason: str
