"""User-supplied numbers are validated against each field's range before they reach M11."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from svc_orchestrator.models.api import (
    EFFORT_CAP,
    FIELD_RANGES,
    AnswerBody,
    ValidateInput,
    check_in_range,
)

BASE: dict[str, Any] = {"idea_text": "an idea", "idea_id": "idea_1", "version": 1}
BAD = [math.nan, math.inf, -math.inf, True, False, "10", None]


def test_the_ranges_are_the_documented_ones() -> None:
    assert FIELD_RANGES == {
        "estimated_effort_hours": (0, EFFORT_CAP),
        "hours_per_week": (0, 80),
        "weeks_available": (1, 52),
        "team_size": (1, 10),
    }


@pytest.mark.parametrize(("field", "bounds"), list(FIELD_RANGES.items()))
def test_override_boundaries(field: str, bounds: tuple[float, float]) -> None:
    low, high = bounds
    for ok in (low, high, (low + high) / 2):
        assert ValidateInput(**BASE, overrides={field: ok}).overrides == {field: ok}
    for bad in (low - 1, high + 1, low - 0.001, high + 0.001, *BAD):
        with pytest.raises(ValidationError):
            ValidateInput(**BASE, overrides={field: bad})


@pytest.mark.parametrize(
    "field",
    ["n_sources", "n_platforms", "demand_pain", "risk", "competition", "learning", "vibes"],
)
def test_evidence_facts_and_scores_cannot_be_overridden(field: str) -> None:
    with pytest.raises(ValidationError):
        ValidateInput(**BASE, overrides={field: 3})


@pytest.mark.parametrize("answer", [0, 0.0, 1, 7.5, 10000])
def test_answer_body_accepts_finite_numbers(answer: float) -> None:
    assert AnswerBody(question_id="q", answer=answer).answer == answer


@pytest.mark.parametrize("answer", [math.nan, math.inf, -math.inf, True, False, "5", None])
def test_answer_body_rejects_non_numbers(answer: Any) -> None:
    with pytest.raises(ValidationError):
        AnswerBody(question_id="q", answer=answer)


def test_answer_body_rejects_json_nan_and_infinity_tokens() -> None:
    for token in ("NaN", "Infinity", "-Infinity", "true"):
        with pytest.raises(ValidationError):
            AnswerBody.model_validate_json(f'{{"question_id": "q", "answer": {token}}}')


@pytest.mark.parametrize(("field", "bounds"), list(FIELD_RANGES.items()))
def test_check_in_range_boundaries(field: str, bounds: tuple[float, float]) -> None:
    low, high = bounds
    assert check_in_range(field, low) == low
    assert check_in_range(field, high) == high
    for bad in (low - 1, high + 1, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            check_in_range(field, bad)


def test_check_in_range_rejects_unknown_fields_and_bools() -> None:
    with pytest.raises(ValueError):
        check_in_range("n_sources", 3)
    with pytest.raises(ValueError):
        check_in_range("team_size", True)
