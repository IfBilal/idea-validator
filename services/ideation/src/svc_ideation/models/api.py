"""Request and response bodies of the M8 internal endpoints (docs/harness/05, M8 section)."""

from typing import Annotated, Literal, Self

from pydantic import AliasChoices, Field, model_validator

from common.schemas import EvidenceItem, StudentProfile
from common.schemas.base import Message
from common.schemas.types import Dimension, EvidenceId

from ..config import MAX_ROUNDS
from .ideas import Change, Idea, Objection, Ref, RunId, Short, Text

MAX_IDEA_TEXT = 4000  # docs/harness/05 §0: idea text <= 4,000 chars
NicheStatus = Literal["rising", "flat", "saturated"]


class Niche(Message):
    """A matched niche from M7 (assumed shape: name + status + normalised trend)."""

    name: Short
    status: NicheStatus | None = None
    trend_growth: float | None = None


class Gap(Message):
    """A gap from M7. ``evidence`` carries texts when known; bare ``evidence_ids`` are fetched."""

    gap_id: Ref
    text: Text
    niche: Short | None = None
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)


class IdeateRequest(Message):
    run_id: RunId
    profile: StudentProfile
    niches: list[Niche] = Field(default_factory=list, max_length=20)
    gaps: list[Gap] = Field(default_factory=list, max_length=20)
    round: int = Field(default=1, ge=1, le=MAX_ROUNDS)
    previous_objections: list[Objection] = Field(default_factory=list, max_length=30)
    count: int = Field(default=5, ge=1, le=20)


class DroppedIdea(Message):
    title: str
    reason: Literal[
        "invalid_schema",
        "unknown_gap",
        "unsupported_evidence",
        "exceeds_available_hours",
        "near_duplicate",
        "duplicate_title",
    ]
    detail: str | None = None


class IdeateResponse(Message):
    ideas: list[Idea]
    dropped: list[DroppedIdea] = Field(default_factory=list)
    reason: str | None = None
    round: int
    prompt_version: str


class GapAnalystRequest(Message):
    run_id: RunId
    ideas: list[Idea] = Field(min_length=1, max_length=20)


class GapAnalystResponse(Message):
    ideas: list[Idea]
    discarded: int = 0
    prompt_version: str


class SkepticRequest(Message):
    run_id: RunId
    ideas: list[Idea] = Field(min_length=1, max_length=20)
    profile: StudentProfile | None = None
    scores: dict[str, dict[Dimension, Annotated[float, Field(ge=0, le=5)]]] | None = None


class SkepticResponse(Message):
    objections: list[Objection]
    discarded: int = 0
    prompt_version: str


class SuggestChangesRequest(Message):
    """Dimension delta from M11 (``from`` -> ``to``) plus the idea text it applies to."""

    run_id: RunId | None = None
    idea_text: str = Field(min_length=1, max_length=MAX_IDEA_TEXT)
    target_dimension: Dimension
    current_score: float = Field(
        ge=0, le=5, validation_alias=AliasChoices("current_score", "from_score")
    )
    required_delta: float | None = Field(default=None, gt=0, le=5)
    to_score: float | None = Field(default=None, ge=0, le=5)
    profile: StudentProfile | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.required_delta is None) == (self.to_score is None):
            raise ValueError("give exactly one of required_delta or to_score")
        if self.target_score <= self.current_score:
            raise ValueError("the target score must be above the current score")
        if self.target_score > 5:
            raise ValueError("the target score cannot exceed 5")
        return self

    @property
    def target_score(self) -> float:
        if self.to_score is not None:
            return self.to_score
        return self.current_score + (self.required_delta or 0.0)


class SuggestChangesResponse(Message):
    changes: list[Change]
    discarded: int = 0
    prompt_version: str


class RewriteRequest(Message):
    run_id: RunId | None = None
    idea_text: str = Field(min_length=1, max_length=MAX_IDEA_TEXT)
    change_text: str = Field(min_length=1, max_length=1000)


class RewriteResponse(Message):
    new_text: str
    change_note: str
    prompt_version: str
