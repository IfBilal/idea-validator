"""Engine inputs (docs/harness/08 §1). M11 trusts nothing: every input is validated."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from common.schemas.base import Message
from common.schemas.types import DIMENSIONS, Dimension, Goal


class ProfileIn(Message):
    goal: Goal
    hours_per_week: float = Field(ge=0)
    weeks_available: float = Field(ge=0)
    team_size: float = Field(ge=1)


class ScoreIn(Message):
    """One dimension score. ``score=None`` means the scorer abstained (no valid evidence)."""

    dimension: Dimension
    score: Annotated[float, Field(ge=0, le=5)] | None
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class GateInputs(Message):
    n_sources: int = Field(ge=0)
    n_platforms: int = Field(ge=0)
    estimated_effort_hours: float = Field(ge=0)
    risk_blocker: str | None = None
    duplicate_of: str | None = None


class Objection(Message):
    """An objection the critic (M12) accepted as evidence-backed."""

    dimension: Dimension
    objection_id: str


class EngineInput(Message):
    idea_id: str | None = None
    version: int | None = None
    run_id: str | None = None
    profile: ProfileIn
    scores: list[ScoreIn]
    gate_inputs: GateInputs
    objections_accepted: list[Objection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_six_distinct_dimensions(self) -> Self:
        seen = [s.dimension for s in self.scores]
        if sorted(seen) != sorted(DIMENSIONS):
            raise ValueError(f"scores must contain each of {list(DIMENSIONS)} exactly once")
        return self

    def score_of(self, dimension: str) -> ScoreIn:
        return next(s for s in self.scores if s.dimension == dimension)
