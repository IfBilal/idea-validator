from pydantic import EmailStr, Field

from common.schemas.base import Message
from common.schemas.profile import Tag
from common.schemas.types import Compute, Goal


class ProfileUpdate(Message):
    """Body of ``PUT /internal/profiles/{user_id}``; the version is assigned by M2."""

    skills: list[Tag] = Field(max_length=30)
    hours_per_week: int = Field(ge=0, le=80)
    weeks_available: int = Field(ge=1, le=52)
    team_size: int = Field(ge=1, le=10)
    compute: Compute
    goal: Goal
    interests: list[Tag] = Field(max_length=30)


class Credentials(Message):
    email: EmailStr
    password: str = Field(max_length=128)


class Registered(Message):
    user_id: str
