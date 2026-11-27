from typing import Annotated, Any

from pydantic import Field, StringConstraints

from .base import Message
from .profile import StudentProfile
from .types import Dimension, EvidenceId, RubricVersion


class DimensionScore(Message):
    """M9/M10 output. ``score=None`` means the scorer abstained (no valid evidence)."""

    dimension: Dimension
    score: Annotated[float, Field(ge=0, le=5)] | None
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[EvidenceId]
    features: dict[str, Any] | None = None
    reason: str = Field(max_length=600)
    rubric_version: RubricVersion
    model: str
    abstained: bool | None = None
    adjusted_by_challenge: float | None = None
    fallback: bool | None = None


class ScoreEvidence(Message):
    evidence_id: EvidenceId
    quote: str


class ScoreRequest(Message):
    rubric_id: Annotated[str, StringConstraints(pattern=r"^[a-z_]+@v\d+$")]
    idea: str = Field(max_length=4000)
    profile: StudentProfile | None = None
    evidence: list[ScoreEvidence]
    features: dict[str, Any]
    run_id: str
