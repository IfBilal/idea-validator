from typing import Annotated

from pydantic import Field, StringConstraints

from .base import Message
from .types import Compute, Goal

Tag = Annotated[str, StringConstraints(max_length=50)]


class StudentProfile(Message):
    user_id: str
    skills: list[Tag] = Field(max_length=30)
    hours_per_week: int = Field(ge=0, le=80)
    weeks_available: int = Field(ge=1, le=52)
    team_size: int = Field(ge=1, le=10)
    compute: Compute
    goal: Goal
    interests: list[Tag] = Field(max_length=30)
    version: int | None = Field(default=None, ge=1)
