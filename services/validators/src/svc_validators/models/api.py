"""M9 request/response messages (docs/harness/05: ``POST /score-all``)."""

from typing import Any

from pydantic import Field

from common.schemas import DimensionScore, ScoreEvidence, StudentProfile
from common.schemas.base import Message
from common.schemas.types import CardId, Dimension


class ScoreAllRequest(Message):
    run_id: str
    idea: str = Field(max_length=4000)
    profile: StudentProfile | None = None
    evidence: list[ScoreEvidence]
    features: dict[str, Any]


class ExtraOutputs(Message):
    """Scorer by-products that M11's gates read. Derived from M7 features, never from a model."""

    estimated_effort_hours: float | None = None
    required_skills: list[str] | None = None
    risk_blocker: str | None = None
    duplicate_of: CardId | None = None


class ScorerError(Message):
    scorer: str
    dimension: Dimension | None = None
    error: str


class ScoreAllResponse(Message):
    scores: list[DimensionScore]
    abstained: list[Dimension]
    extra: ExtraOutputs
    errors: list[ScorerError] = Field(default_factory=list)
