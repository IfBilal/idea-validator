"""Request/response models of the internal M3 endpoints (docs/harness/05 §3)."""

import math
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from common.schemas.base import Message

EFFORT_CAP = 10_000  # hours: a sane upper bound for one idea
FIELD_RANGES: dict[str, tuple[float, float]] = {
    "estimated_effort_hours": (0, EFFORT_CAP),
    "hours_per_week": (0, 80),
    "weeks_available": (1, 52),
    "team_size": (1, 10),
}
GATE_FIELDS = ("estimated_effort_hours",)  # n_sources / n_platforms are evidence facts
PROFILE_FIELDS = ("hours_per_week", "weeks_available", "team_size")
Scope = Literal["gate_input", "profile"]  # scores are never user-set: what-if goes via M11 /whatif
_ALLOWED: dict[str, tuple[str, ...]] = {"gate_input": GATE_FIELDS, "profile": PROFILE_FIELDS}
ALL_UNKNOWN_FIELDS = frozenset(FIELD_RANGES)
StrictFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def check_in_range(field: str, value: Any) -> float:
    """A finite, non-bool number inside the field's documented range, else ``ValueError``."""
    if field not in FIELD_RANGES:
        raise ValueError(f"{field!r} cannot be set by the user")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    low, high = FIELD_RANGES[field]
    if not low <= value <= high:
        raise ValueError(f"{field} must be between {low:g} and {high:g}")
    return value


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
        for end in self.range:
            check_in_range(self.field, end)
        if self.range[0] > self.range[1]:
            raise ValueError("range must be [low, high] with low <= high")
        return self


class ValidateInput(Message):
    idea_text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    idea_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    overrides: dict[str, StrictFloat] = Field(default_factory=dict)
    unknowns: list[UnknownSpec] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _known_override_fields(self) -> Self:
        for field, value in self.overrides.items():
            check_in_range(field, value)
        return self


class StartRun(Message):
    kind: Literal["validate"]
    user_id: str = Field(min_length=1)
    input: ValidateInput


class RunStarted(Message):
    run_id: str


class AnswerBody(Message):
    question_id: str
    answer: StrictFloat


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
