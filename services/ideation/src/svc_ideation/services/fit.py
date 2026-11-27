"""Fit-to-profile rule (docs/harness/09 §4): effort by scope must not exceed available hours."""

from typing import Final

from common.schemas import StudentProfile

EFFORT_HOURS: Final[dict[str, int]] = {"S": 60, "M": 160, "L": 400}


def available_hours(profile: StudentProfile) -> int:
    return profile.hours_per_week * profile.weeks_available * profile.team_size


def fits_profile(scope: str, profile: StudentProfile) -> bool:
    """True when the scope's base effort is at most the student's available hours (inclusive)."""
    effort = EFFORT_HOURS.get(scope)
    return effort is not None and effort <= available_hours(profile)
