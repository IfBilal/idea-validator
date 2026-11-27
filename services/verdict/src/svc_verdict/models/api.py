"""HTTP request/response models for M11 (docs/harness/08 §6). Internal endpoints only."""

from typing import Literal, Self, get_args

from pydantic import Field, model_validator

from common.schemas import DimensionScore, Verdict
from common.schemas.base import Message
from common.schemas.types import DIMENSIONS, Dimension

from .inputs import EngineInput, GateInputs, Objection, ProfileIn, ScoreIn

ProfileField = Literal["hours_per_week", "weeks_available", "team_size"]
GateField = Literal["n_sources", "n_platforms", "estimated_effort_hours"]


class VerdictRequest(Message):
    idea_id: str
    version: int = Field(ge=1)
    run_id: str
    profile: ProfileIn
    scores: list[DimensionScore]
    gate_inputs: GateInputs
    objections_accepted: list[Objection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_six_distinct_dimensions(self) -> Self:
        if sorted(s.dimension for s in self.scores) != sorted(DIMENSIONS):
            raise ValueError(f"scores must contain each of {list(DIMENSIONS)} exactly once")
        return self

    def to_engine_input(self) -> EngineInput:
        return EngineInput(
            idea_id=self.idea_id,
            version=self.version,
            run_id=self.run_id,
            profile=self.profile,
            scores=[
                ScoreIn(
                    dimension=s.dimension,
                    score=s.score,
                    confidence=s.confidence,
                    evidence_ids=list(s.evidence_ids),
                )
                for s in self.scores
            ],
            gate_inputs=self.gate_inputs,
            objections_accepted=self.objections_accepted,
        )


class Change(Message):
    """One explicit what-if change: a dimension score or a profile field."""

    dimension: Dimension | None = None
    profile_field: ProfileField | None = None
    to: float = Field(ge=0)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> Self:
        if (self.dimension is None) == (self.profile_field is None):
            raise ValueError("give exactly one of dimension or profile_field")
        if self.dimension is not None and self.to > 5:
            raise ValueError("a dimension score must be between 0 and 5")
        return self


class WhatIfRequest(Message):
    input: VerdictRequest
    change: Change | None = None


class Unknown(Message):
    """The value M3 is unsure about: a dimension score, a gate input or a profile field."""

    scope: Literal["score", "gate_input", "profile"]
    field: str

    @model_validator(mode="after")
    def _field_matches_scope(self) -> Self:
        allowed: dict[str, tuple[str, ...]] = {
            "score": DIMENSIONS,
            "gate_input": get_args(GateField),
            "profile": get_args(ProfileField),
        }
        if self.field not in allowed[self.scope]:
            raise ValueError(f"{self.field!r} is not a valid {self.scope} field")
        return self


class VoiRequest(Message):
    input: VerdictRequest
    unknown: Unknown
    range: tuple[float, float]

    @model_validator(mode="after")
    def _ordered_range(self) -> Self:
        if self.range[0] > self.range[1]:
            raise ValueError("range must be [low, high] with low <= high")
        return self


class VoiPoint(Message):
    value: float
    verdict: str
    stability: str
    score: float | None


class VoiResponse(Message):
    low: VoiPoint
    high: VoiPoint
    verdict_changes: bool


__all__ = [
    "Change",
    "Unknown",
    "Verdict",
    "VerdictRequest",
    "VoiPoint",
    "VoiRequest",
    "VoiResponse",
    "WhatIfRequest",
]
