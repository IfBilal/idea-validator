"""Public bodies and responses for runs. Inputs are bounded; unknown fields are refused.

Responses that echo M3 (``progress``, ``spent``, ``budget``, ``error``) are rebuilt from an
allow-list, so internal URLs, stack traces or keys can never reach a browser through them.
"""

import math
import unicodedata
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, model_validator

from common.schemas.base import Message

from .base import Upstream

_NUMBER_BOUND = 10**12  # M3's widest range is 0..10000; anything near this is an attack or a typo
_LAYOUT = frozenset("\n\r\t")
# control (incl. C1), format (zero-width, bidi), line/paragraph separators, surrogates, private use
_FORBIDDEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

TERMINAL_STATES = frozenset({"succeeded", "failed", "budget_stopped"})
RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")]
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"


def _check_chars(value: str, allow_layout: bool) -> None:
    for ch in value:
        if allow_layout and ch in _LAYOUT:
            continue
        if unicodedata.category(ch) in _FORBIDDEN:
            raise ValueError("control, invisible or direction-changing characters are not allowed")


def _single_line(value: str) -> str:
    _check_chars(value, allow_layout=False)
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _free_text(value: str) -> str:
    _check_chars(value, allow_layout=True)
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
    try:
        if isinstance(value, str):
            if len(value) > 1000:
                raise ValueError("answer must be at most 1000 characters")
            number = float(value.strip())
        elif isinstance(value, int | float):
            number = float(value)  # a 400-digit int overflows here
        else:
            raise ValueError("answer must be a number")
    except OverflowError:
        raise ValueError("answer is out of range") from None
    except ValueError as exc:
        text = str(exc)
        raise ValueError(text if text.startswith("answer") else "answer must be a number") from None
    if not math.isfinite(number):
        raise ValueError("answer must be a finite number")
    if abs(number) > _NUMBER_BOUND:
        raise ValueError("answer is out of range")
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


# ---- what M3 may tell a browser about a run
_COUNTERS = ("cost_usd", "tokens", "pages", "steps")
_LIMITS = ("max_cost_usd", "max_tokens", "max_pages", "max_steps")
FIXED_ERRORS = {
    "failed": "the run failed",
    "budget_stopped": "the run stopped at its budget",
}
OTHER_ERROR = "the run reported a problem"


def _is_number(value: Any) -> bool:
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    return ok and math.isfinite(value) and 0 <= value <= _NUMBER_BOUND


def _numbers(raw: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in keys if k in raw and _is_number(raw[k])}


def _progress(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    step = raw.get("step")
    if isinstance(step, str) and 1 <= len(step) <= 40 and step.replace("_", "").isalnum():
        out["step"] = step
    rnd = raw.get("round")
    if isinstance(rnd, int) and not isinstance(rnd, bool) and 0 <= rnd <= 1000:
        out["round"] = rnd
    percent = raw.get("percent")
    if isinstance(percent, int | float) and _is_number(percent) and percent <= 100:
        out["percent"] = percent
    return out


def fixed_error(state: Any, error: Any) -> str | None:
    """Never M3's own text: a fixed message chosen by the run state."""
    if isinstance(state, str) and state in FIXED_ERRORS:
        return FIXED_ERRORS[state]
    return OTHER_ERROR if error else None


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

    @model_validator(mode="before")
    @classmethod
    def _allow_list(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            **data,
            "progress": _progress(data.get("progress")),
            "spent": _numbers(data.get("spent"), _COUNTERS),
            "budget": _numbers(data.get("budget"), _COUNTERS + _LIMITS),
            "error": fixed_error(data.get("state"), data.get("error")),
        }
