"""Public bodies and responses for runs. Inputs are bounded; unknown fields are refused."""

import math
import re
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, model_validator

from common.schemas.base import Message

from .base import Upstream

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # newline, CR and tab allowed

TERMINAL_STATES = frozenset({"succeeded", "failed", "budget_stopped"})
RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")]


def _single_line(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _free_text(value: str) -> str:
    if _TEXT_CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


Title = Annotated[str, Field(max_length=200), AfterValidator(_single_line)]
IdeaText = Annotated[str, Field(max_length=4000), AfterValidator(_free_text)]
ChangeText = Annotated[str, Field(max_length=1000), AfterValidator(_free_text)]


def _numeric_answer(value: Any) -> float:
    """M3 asks numeric questions only: a number, or a numeric string of at most 1000 characters."""
    if isinstance(value, bool):
        raise ValueError("answer must be a number")
    if isinstance(value, str):
        if len(value) > 1000:
            raise ValueError("answer must be at most 1000 characters")
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError("answer must be a number") from None
    if not isinstance(value, int | float):
        raise ValueError("answer must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("answer must be a finite number")
    return number


class ValidateBody(Message):
    idea_text: IdeaText
    title: Title | None = None
    idea_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _title_for_new_ideas(self) -> Self:
        if self.idea_id is None and self.title is None:
            raise ValueError("title is required when idea_id is not given")
        return self


class DiscoverBody(Message):
    niche: Annotated[str, Field(max_length=80), AfterValidator(_single_line)] | None = None
    count: Annotated[int, Field(ge=1, le=10, strict=True)] = 5
    deep_search: StrictBool = False


class AnswerBody(Message):
    question_id: OpaqueId
    answer: Annotated[float, BeforeValidator(_numeric_answer)]


class ApproveBody(Message):
    approval_id: OpaqueId
    decision: Literal["approve", "decline"]


class ChallengeBody(Message):
    version: Annotated[int, Field(ge=1, strict=True)]
    claim_id: OpaqueId


class WhatIfBody(Message):
    version: Annotated[int, Field(ge=1, strict=True)]
    change_id: OpaqueId | None = None
    custom_change: ChangeText | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.change_id is None) == (self.custom_change is None):
            raise ValueError("give exactly one of change_id or custom_change")
        return self


class ValidateAccepted(Message):
    run_id: str
    idea_id: str
    version: int


class RunAccepted(Message):
    run_id: str


class StepAccepted(Upstream):
    run_id: str
    state: str


class RunView(Upstream):
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
