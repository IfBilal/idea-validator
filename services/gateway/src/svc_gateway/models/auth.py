"""Public request/response bodies. Every field is bounded and control characters are refused."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, EmailStr, Field

from common.schemas.base import Message
from common.schemas.types import Compute, Goal

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NUL = re.compile(r"\x00")


def _no_control_chars(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    return value


def _no_nul(value: str) -> str:
    if _NUL.search(value):
        raise ValueError("NUL characters are not allowed")
    return value


def _stripped_tag(value: str) -> str:
    cleaned = _no_control_chars(value).strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


Tag = Annotated[str, Field(max_length=50), AfterValidator(_stripped_tag)]


class Credentials(Message):
    email: EmailStr
    password: Annotated[str, Field(max_length=128), AfterValidator(_no_nul)]


class RegisterOut(Message):
    user_id: str


class TokenOut(Message):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (OAuth token type, not a secret)
    expires_in: int


class ProfileBody(Message):
    """``PUT /profile``: a student profile without ``user_id`` (taken from the token)."""

    skills: list[Tag] = Field(max_length=30)
    hours_per_week: int = Field(ge=0, le=80)
    weeks_available: int = Field(ge=1, le=52)
    team_size: int = Field(ge=1, le=10)
    compute: Compute
    goal: Goal
    interests: list[Tag] = Field(max_length=30)
