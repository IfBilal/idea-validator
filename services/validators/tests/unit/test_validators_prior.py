"""Feature priors at every rule boundary of every shipped rubric (first match wins)."""

from pathlib import Path
from typing import Any

import pytest

from svc_validators.services.prior import PriorCalculator
from svc_validators.services.rubrics import load_rubric

RUBRICS = Path(__file__).resolve().parents[4] / "rubrics"


def prior(dimension: str, **features: Any) -> float | None:
    return PriorCalculator(load_rubric(RUBRICS, dimension, 1)).compute(features).score


DEMAND = {"n_sources": 0, "n_platforms": 0, "complaint_count": 0, "trend_growth": 0.0}
DEMAND["recency_share"] = 0.0


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"n_sources": 5, "n_platforms": 2, "trend_growth": 0.11}, 5),
        ({"n_sources": 9, "n_platforms": 3, "trend_growth": 0.5}, 5),
        ({"n_sources": 5, "n_platforms": 2, "trend_growth": 0.1}, 4),  # strict >
        ({"n_sources": 5, "n_platforms": 1, "trend_growth": 0.9}, 3),  # one platform
        ({"n_sources": 4, "n_platforms": 2}, 4),
        ({"n_sources": 4, "n_platforms": 1}, 3),
        ({"n_sources": 3, "n_platforms": 2}, 3),
        ({"n_sources": 2}, 3),
        ({"n_sources": 1, "recency_share": 0.5}, 2),
        ({"n_sources": 1, "recency_share": 0.49}, 1),
        ({"n_sources": 0}, 0),
    ],
)
def test_demand_pain_boundaries(overrides: dict[str, Any], expected: float) -> None:
    assert prior("demand_pain", **{**DEMAND, **overrides}) == expected


COMP = {"n_competitors": 0, "niche_saturation": 0.0, "competitor_strength": 0.0}


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, 5),
        ({"niche_saturation": 0.5}, 4),  # no competitors but a busy niche
        ({"n_competitors": 2, "competitor_strength": 0.39, "niche_saturation": 1}, 4),
        ({"n_competitors": 2, "competitor_strength": 0.4, "niche_saturation": 0.5}, 3),
        ({"n_competitors": 3, "competitor_strength": 0.1, "niche_saturation": 0.5}, 3),
        ({"n_competitors": 5, "niche_saturation": 0.5, "competitor_strength": 0.9}, 3),
        ({"n_competitors": 5, "niche_saturation": 1, "competitor_strength": 0.9}, 2),
        ({"n_competitors": 10, "niche_saturation": 1, "competitor_strength": 0.9}, 2),
        ({"n_competitors": 11, "niche_saturation": 1, "competitor_strength": 0.9}, 1),
        ({"n_competitors": 11, "niche_saturation": 0.5, "competitor_strength": 0.9}, 0),
    ],
)
def test_competition_boundaries(overrides: dict[str, Any], expected: float) -> None:
    assert prior("competition", **{**COMP, **overrides}) == expected


DIFF = {"unaddressed_gap_count": 0, "max_similarity": 0.0, "complaint_count": 0}


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"unaddressed_gap_count": 1, "max_similarity": 0.59, "complaint_count": 3}, 5),
        ({"unaddressed_gap_count": 1, "max_similarity": 0.6, "complaint_count": 3}, 4),
        ({"unaddressed_gap_count": 1, "max_similarity": 0.59, "complaint_count": 2}, 4),
        ({"unaddressed_gap_count": 1, "max_similarity": 0.74, "complaint_count": 0}, 4),
        ({"unaddressed_gap_count": 1, "max_similarity": 0.75}, 3),
        ({"unaddressed_gap_count": 0, "max_similarity": 0.5}, 3),
        ({"max_similarity": 0.79}, 3),
        ({"max_similarity": 0.8}, 2),
        ({"max_similarity": 0.87}, 2),
        ({"max_similarity": 0.88}, 1),
        ({"max_similarity": 0.94}, 1),
        ({"max_similarity": 0.95}, 0),
        ({"max_similarity": 1.0}, 0),
    ],
)
def test_differentiation_boundaries(overrides: dict[str, Any], expected: float) -> None:
    assert prior("differentiation", **{**DIFF, **overrides}) == expected


FEAS = {
    "estimated_effort_hours": 0.0,
    "available_hours": 100,
    "skill_overlap": 1.0,
    "compute_fit": True,
}


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"estimated_effort_hours": 50, "skill_overlap": 0.8}, 5),
        ({"estimated_effort_hours": 50.01, "skill_overlap": 0.8}, 4),
        ({"estimated_effort_hours": 50, "skill_overlap": 0.79}, 4),
        ({"estimated_effort_hours": 70, "skill_overlap": 0.6}, 4),
        ({"estimated_effort_hours": 70.01, "skill_overlap": 0.6}, 3),
        ({"estimated_effort_hours": 50, "skill_overlap": 0.59}, 3),
        ({"estimated_effort_hours": 90, "skill_overlap": 0.0}, 3),
        ({"estimated_effort_hours": 90.01}, 2),
        ({"estimated_effort_hours": 100}, 2),
        ({"estimated_effort_hours": 100.01}, 1),
        ({"estimated_effort_hours": 300}, 1),
        ({"estimated_effort_hours": 300.01}, 0),
        ({"estimated_effort_hours": 10, "compute_fit": False}, 2),  # no compute: capped at 2
    ],
)
def test_feasibility_boundaries(overrides: dict[str, Any], expected: float) -> None:
    assert prior("feasibility", **{**FEAS, **overrides}) == expected


@pytest.mark.parametrize(
    "novelty,cv,expected",
    [
        (3, 0.7, 5),
        (3, 0.69, 4),
        (2, 0.99, 4),
        (1.99, 0.99, 3),
        (1, 0, 3),
        (0.99, 0.5, 2),
        (0, 0.5, 2),
        (0, 0.49, 1),
        (0, 0.01, 1),
        (0, 0, 0),
    ],
)
def test_learning_boundaries(novelty: float, cv: float, expected: float) -> None:
    assert prior("learning", skill_novelty=novelty, cv_signal=cv) == expected


@pytest.mark.parametrize(
    "flags,dependency,expected",
    [
        (["blocker"], False, 0),
        (["blocker", "pii", "minors"], False, 0),
        ([], False, 5),
        ([], True, 4),
        (["pii"], False, 3),
        (["pii"], True, 3),
        (["pii", "minors"], False, 2),
        (["pii", "minors", "finance"], False, 1),
        (["a", "b", "c", "d"], True, 1),
    ],
)
def test_risk_boundaries(flags: list[str], dependency: bool, expected: float) -> None:
    assert prior("risk", risk_flags=flags, platform_dependency=dependency) == expected


def test_missing_feature_makes_the_prior_unavailable_not_zero() -> None:
    result = PriorCalculator(load_rubric(RUBRICS, "demand_pain", 1)).compute({"n_sources": 5})
    assert result.score is None
    assert "n_platforms" in result.problem


def test_none_feature_makes_the_prior_unavailable() -> None:
    result = PriorCalculator(load_rubric(RUBRICS, "learning", 1)).compute(
        {"skill_novelty": None, "cv_signal": 0.9}
    )
    assert result.score is None


def test_wrongly_typed_feature_makes_the_prior_unavailable() -> None:
    result = PriorCalculator(load_rubric(RUBRICS, "learning", 1)).compute(
        {"skill_novelty": "lots", "cv_signal": 0.9}
    )
    assert result.score is None
    assert result.problem


def test_short_circuit_means_unneeded_features_may_be_absent() -> None:
    # n_sources == 2 matches rule 3 before any rule needs recency_share.
    assert prior("demand_pain", n_sources=2, n_platforms=1) == 3


def test_first_matching_rule_wins() -> None:
    result = PriorCalculator(load_rubric(RUBRICS, "risk", 1)).compute(
        {"risk_flags": ["blocker"], "platform_dependency": False}
    )
    assert (result.score, result.rule_index) == (0, 0)
