"""Request/response models of the internal M3 endpoints (docs/harness/05 §3)."""

from typing import Annotated, Any, Literal, Self, get_args

from pydantic import Field, StringConstraints, model_validator

from common.schemas.base import Message
from common.schemas.types import DIMENSIONS

GATE_FIELDS = ("n_sources", "n_platforms", "estimated_effort_hours")
PROFILE_FIELDS = ("hours_per_week", "weeks_available", "team_size")
Scope = Literal["score", "gate_input", "profile"]
_ALLOWED: dict[str, tuple[str, ...]] = {
    "score": DIMENSIONS,
    "gate_input": GATE_FIELDS,
    "profile": PROFILE_FIELDS,
}
ALL_UNKNOWN_FIELDS = frozenset(DIMENSIONS) | set(GATE_FIELDS) | set(PROFILE_FIELDS)


class UnknownSpec(Message):
    """A value the run is unsure about: ask the user only if the verdict could change."""

    scope: Scope
    field: str
    range: tuple[float, float]
    question: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.field not in _ALLOWED[self.scope]:
            raise ValueError(f"{self.field!r} is not a valid {self.scope} field")
        if self.range[0] > self.range[1]:
            raise ValueError("range must be [low, high] with low <= high")
        if self.scope == "score" and not (self.range[0] >= 0 and self.range[1] <= 5):
            raise ValueError("a score range must lie within 0..5")
        return self


class ValidateInput(Message):
    idea_text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    idea_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    overrides: dict[str, float] = Field(default_factory=dict)
    unknowns: list[UnknownSpec] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _known_override_fields(self) -> Self:
        if unknown := sorted(set(self.overrides) - ALL_UNKNOWN_FIELDS):
            raise ValueError(f"unknown override fields: {unknown}")
        return self


class StartRun(Message):
    kind: Literal["validate"]
    user_id: str = Field(min_length=1)
    input: ValidateInput


class RunStarted(Message):
    run_id: str


class AnswerBody(Message):
    question_id: str
    answer: float


class ApproveBody(Message):
    approval_id: str
    decision: Literal["approve", "decline"]


class Accepted(Message):
    run_id: str
    state: str


class RunView(Message):
    run_id: str
    kind: str
    state: str
    progress: dict[str, Any]
    spent: dict[str, Any]
    budget: dict[str, Any]
    pending_question: dict[str, Any] | None
    pending_approval: dict[str, Any] | None
    result: dict[str, Any] | None
    error: str | None = None


assert set(get_args(Scope)) == set(_ALLOWED)  # keep the literal and the table in step
