"""State of the Discover graph (docs/harness/07 §3). Domain payloads are plain JSON."""

import operator
from typing import Annotated, Any

from pydantic import BaseModel, Field

from .state import STOPPING, RunStatus


class DiscoverState(BaseModel):
    # --- input --------------------------------------------------------------------------------
    run_id: str
    user_id: str
    niche: str | None = None
    count: int = 5

    # --- progress -----------------------------------------------------------------------------
    step: str = "REQUEST"
    status: RunStatus = RunStatus.RUNNING
    error: str | None = None
    stop_reason: str | None = None
    degraded: bool = False
    notes: Annotated[list[str], operator.add] = Field(default_factory=list)
    memo: Annotated[dict[str, Any], operator.or_] = Field(default_factory=dict)

    # --- request / niches ---------------------------------------------------------------------
    profile: dict[str, Any] | None = None
    niches: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)

    # --- debate -------------------------------------------------------------------------------
    round: int = 1
    candidates: list[dict[str, Any]] = Field(default_factory=list)  # surviving M8 ideas
    pending: list[dict[str, Any]] = Field(default_factory=list)  # this round's new ideas
    raised: list[dict[str, Any]] = Field(default_factory=list)  # this round's skeptic objections
    dropped: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    seen_objections: list[str] = Field(default_factory=list)
    accepted: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict
    )  # idea_ref -> objections
    previous_objections: list[dict[str, Any]] = Field(default_factory=list)
    continue_debate: bool = False
    rounds_run: int = 0

    # --- result -------------------------------------------------------------------------------
    items: list[dict[str, Any]] = Field(default_factory=list)
    card: dict[str, Any] | None = None  # the run result: {ideas, dropped, ...}

    @property
    def stopped(self) -> bool:
        return self.status in STOPPING
