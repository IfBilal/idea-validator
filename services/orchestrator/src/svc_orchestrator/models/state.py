"""Run lifecycle and the Validate graph state (docs/harness/07 §1-2).

The state is a Pydantic model. Domain payloads (evidence, scores, the verdict) are kept as plain
JSON so checkpoints stay portable; nodes parse them into the shared ``common.schemas`` models.
"""

import operator
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUDGET_STOPPED = "budget_stopped"
    CANCELLED = "cancelled"


STOPPING = frozenset({RunStatus.FAILED, RunStatus.BUDGET_STOPPED})
TERMINAL = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.BUDGET_STOPPED, RunStatus.CANCELLED}
)


class ValidateState(BaseModel):
    # --- input --------------------------------------------------------------------------------
    run_id: str
    user_id: str
    idea_id: str
    idea_version: int = 1
    idea_text: str
    title: str | None = None
    overrides: dict[str, float] = Field(default_factory=dict)  # values the user supplied up front
    unknowns: list[dict[str, Any]] = Field(default_factory=list)  # extra caller-declared unknowns

    # --- progress -----------------------------------------------------------------------------
    step: str = "INTAKE"
    status: RunStatus = RunStatus.RUNNING
    error: str | None = None
    stop_reason: str | None = None
    degraded: bool = False  # a non-critical step failed: result is real but must not be cached
    notes: Annotated[list[str], operator.add] = Field(default_factory=list)
    memo: Annotated[dict[str, Any], operator.or_] = Field(default_factory=dict)

    # --- intake / cache -----------------------------------------------------------------------
    profile: dict[str, Any] | None = None
    versions: dict[str, str] = Field(default_factory=dict)  # engine, rubrics, profile_weights
    rubric_versions: dict[str, str] = Field(default_factory=dict)
    cache_key: str | None = None
    cache_hit: bool = False
    cached_card: dict[str, Any] | None = None

    # --- evidence / features ------------------------------------------------------------------
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    n_sources: int = 0
    n_platforms: int = 0
    wants_deep_search: bool = False  # evidence is thin and scouting is allowed
    deep_search_done: bool = False
    features: dict[str, Any] = Field(default_factory=dict)

    # --- scoring / clarification --------------------------------------------------------------
    scores: list[dict[str, Any]] = Field(default_factory=list)
    scorer_extra: dict[str, Any] = Field(default_factory=dict)
    answers: Annotated[dict[str, float | None], operator.or_] = Field(default_factory=dict)
    asked: Annotated[list[str], operator.add] = Field(default_factory=list)
    vetted: Annotated[list[str], operator.add] = Field(default_factory=list)  # VOI said: no change
    pending_question: dict[str, Any] | None = None

    # --- approval -----------------------------------------------------------------------------
    pending_approval: dict[str, Any] | None = None
    approval_decision: str | None = None  # "approved" | "declined" | None

    # --- verdict / claims / card --------------------------------------------------------------
    verdict: dict[str, Any] | None = None
    claim_results: list[dict[str, Any]] = Field(default_factory=list)
    removed_claims: list[str] = Field(default_factory=list)
    recomputed: bool = False
    verdict_final: bool = False  # True only once the challenge settled the M11 verdict
    missing_inputs: list[str] = Field(default_factory=list)  # inputs we refuse to guess
    card: dict[str, Any] | None = None

    @property
    def stopped(self) -> bool:
        return self.status in STOPPING
