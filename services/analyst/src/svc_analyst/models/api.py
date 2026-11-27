"""Request and response models of the M7 API (strict: unknown fields rejected)."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from common.schemas.base import Message
from common.schemas.profile import StudentProfile
from common.schemas.types import EvidenceId

from .domain import Gap, MonthStat, NicheStatus

MAX_IDEA_CHARS = 4000
MAX_EVIDENCE_IDS = 30
MAX_WISH_CHARS = 500

Skill = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class FeaturesRequest(Message):
    idea_text: str = Field(min_length=1, max_length=MAX_IDEA_CHARS)
    evidence_ids: list[EvidenceId] = Field(max_length=MAX_EVIDENCE_IDS)
    profile: StudentProfile
    required_skills: list[Skill] | None = Field(default=None, max_length=30)
    evidence_scores: dict[EvidenceId, float] | None = Field(
        default=None, max_length=MAX_EVIDENCE_IDS
    )
    scope: Literal["S", "M", "L"] | None = None

    @field_validator("idea_text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idea_text must not be blank")
        return value


class FeaturesResponse(Message):
    """Flat feature dict, names exactly as docs/harness/09 §4 (+ ``required_skills``)."""

    n_sources: int
    n_platforms: int
    complaint_count: int
    trend_growth: float
    recency_share: float
    n_competitors: int
    competitor_strength: float
    niche_saturation: float
    max_similarity: float
    unaddressed_gap_count: int
    skill_overlap: float
    available_hours: int
    estimated_effort_hours: float
    compute_fit: bool
    risk_flags: list[str]
    platform_dependency: bool
    skill_novelty: int
    cv_signal: float
    required_skills: list[str]
    rejected_evidence_count: int  # cited ids M6 does not know (ignored in every count)


class MatchRequest(Message):
    profile: StudentProfile
    niche_wish: str | None = Field(default=None, max_length=MAX_WISH_CHARS)
    limit: int = Field(default=5, ge=1, le=20)


class NicheMatch(Message):
    niche_id: str
    name: str
    status: NicheStatus
    size: int
    score: float
    matched_terms: list[str]


class MatchResponse(Message):
    matches: list[NicheMatch]


class NicheSummary(Message):
    id: str
    name: str
    size: int
    trend_slope: float
    activity_slope: float
    status: NicheStatus
    keywords: list[str]


class NicheDetail(NicheSummary):
    series: list[MonthStat]
    card_ids: list[str]


class NicheList(Message):
    niches: list[NicheSummary]


class GapList(Message):
    gaps: list[Gap]


class RecomputeResponse(Message):
    cards: int
    niches: int
    gaps: int
    unassigned: int
    ignored_future: int
    as_of: date | None
