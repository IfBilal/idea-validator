from typing import Any

from pydantic import ConfigDict, Field

from .base import Message
from .scoring import DimensionScore
from .types import Dimension, GateStatus, Stability, VerdictLabel


class Gates(Message):
    evidence: GateStatus
    feasibility: GateStatus
    risk: GateStatus
    duplicate: GateStatus


class Weights(Message):
    demand_pain: float
    competition: float
    differentiation: float
    feasibility: float
    risk: float
    learning: float


class Thresholds(Message):
    T_go: float
    T_no: float
    floor: float


class WhatWouldMakeGo(Message):
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    change: str | None = None
    dimension: str | None = None
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    new_score: float | None = None
    new_verdict: str
    stable: bool | None = None


class Sensitivity(Message):
    runs: int
    flipped: int
    flips: list[dict[str, Any]] | None = None
    note: str | None = None


class VerdictVersions(Message):
    engine: str
    rubrics: str
    profile_weights: str


class Verdict(Message):
    """M11 output. Only the Verdict Engine produces this; no model may set ``verdict``."""

    idea_id: str
    version: int = Field(ge=1)
    run_id: str
    verdict: VerdictLabel
    stability: Stability
    score: float | None = Field(ge=0, le=100)
    confidence: float | None = Field(ge=0, le=1)
    gates: Gates
    weights: Weights
    thresholds: Thresholds
    scores: list[DimensionScore] = Field(min_length=6, max_length=6)
    weakest_dimension: Dimension | None
    what_would_make_go: list[WhatWouldMakeGo]
    sensitivity: Sensitivity
    reasons: list[str] | None = None
    missing: list[str] | None = None
    versions: VerdictVersions
