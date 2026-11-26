"""Shared literal types and constrained strings (mirror schemas/*.schema.json)."""

from typing import Annotated, Literal

from pydantic import StringConstraints

Dimension = Literal[
    "demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"
]
DIMENSIONS: tuple[str, ...] = (
    "demand_pain",
    "competition",
    "differentiation",
    "feasibility",
    "risk",
    "learning",
)
VerdictLabel = Literal["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"]
Stability = Literal["STABLE", "BORDERLINE"]
GateStatus = Literal["pass", "fail", "not_evaluated"]
Source = Literal["hackernews", "github", "reddit", "producthunt", "yc"]
Stage = Literal["launched", "seed", "series_a", "series_b_plus", "acquired", "dead", "unknown"]
Kind = Literal["product", "complaint", "request", "discussion", "repo"]
Compute = Literal["laptop", "free_gpu", "paid"]
Goal = Literal["startup", "portfolio", "course_project"]

CardId = Annotated[str, StringConstraints(pattern=r"^card_[0-9a-f]{8,12}$")]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^card_[0-9a-f]{8,12}#c\d+$")]
RubricVersion = Annotated[str, StringConstraints(pattern=r"^[a-z_]+@v\d+$")]
