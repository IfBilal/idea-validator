"""State of the Challenge run (docs/harness/06 §4): re-check one claim of a stored card."""

import operator
from typing import Annotated, Any

from pydantic import BaseModel, Field

from .state import STOPPING, RunStatus


class ChallengeState(BaseModel):
    run_id: str
    user_id: str
    idea_id: str
    version: int
    claim_id: str

    step: str = "LOAD"
    status: RunStatus = RunStatus.RUNNING
    error: str | None = None
    stop_reason: str | None = None
    notes: Annotated[list[str], operator.add] = Field(default_factory=list)
    memo: Annotated[dict[str, Any], operator.or_] = Field(default_factory=dict)

    text: str | None = None  # the idea text of the challenged version
    base_card: dict[str, Any] | None = None
    claim: dict[str, Any] | None = None
    outcome: str | None = None  # "defended" | "removed"
    reason: str | None = None
    supporting_quote: str | None = None
    new_version: int | None = None
    new_card: dict[str, Any] | None = None
    saved: bool = False  # the new version (or the existing one) is stored in M2
    already_removed: bool = False
    card: dict[str, Any] | None = None  # the run result

    @property
    def stopped(self) -> bool:
        return self.status in STOPPING
