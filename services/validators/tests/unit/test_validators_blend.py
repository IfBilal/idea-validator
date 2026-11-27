"""Blend (ADR-006): final = round_half(alpha*model + (1-alpha)*prior), and confidence scaling."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svc_validators.services.blend import blend, round_half, scaled_confidence


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, 0.0),
        (0.2499, 0.0),
        (0.25, 0.5),  # ties round up
        (0.2501, 0.5),
        (0.4999, 0.5),
        (0.5, 0.5),
        (0.7499, 0.5),
        (0.75, 1.0),
        (0.7501, 1.0),
        (2.25, 2.5),
        (2.75, 3.0),
        (4.74, 4.5),
        (4.75, 5.0),
        (5.0, 5.0),
        (5.2, 5.0),  # clamped
        (-0.3, 0.0),  # clamped
    ],
)
def test_round_half_edges(value: float, expected: float) -> None:
    assert round_half(value) == expected


@pytest.mark.parametrize(
    "model,prior,alpha,expected",
    [
        (3.0, 2.0, 0.6, 2.5),  # 2.6
        (4.0, 3.0, 0.6, 3.5),  # 3.6
        (4.0, 4.0, 0.6, 4.0),
        (0.0, 5.0, 0.6, 2.0),  # 2.0
        (5.0, 0.0, 0.6, 3.0),  # 3.0
        (3.5, 3.0, 0.5, 3.5),  # 3.25 -> up
        (3.5, 4.0, 0.5, 4.0),  # 3.75 -> up
        (3.0, 3.4, 0.5, 3.0),  # 3.2 -> down
        (3.0, 3.5, 0.5, 3.5),  # 3.25
        (3.0, 4.5, 0.5, 4.0),  # 3.75
        (3.0, 4.4, 0.5, 3.5),  # 3.7
    ],
)
def test_blend_rounding_edges(model: float, prior: float, alpha: float, expected: float) -> None:
    assert blend(model, prior, alpha) == expected


def test_alpha_zero_is_prior_only() -> None:
    assert blend(5.0, 2.0, 0.0) == 2.0
    assert blend(0.0, 3.5, 0.0) == 3.5


def test_alpha_one_is_model_only() -> None:
    assert blend(4.5, 1.0, 1.0) == 4.5
    assert blend(2.25, 5.0, 1.0) == 2.5  # still rounded to a half point


def test_blend_has_no_float_noise_at_the_default_alpha() -> None:
    # 0.6*3.5 + 0.4*3.5 must be exactly 3.5, not 3.4999999999999996
    assert blend(3.5, 3.5, 0.6) == 3.5
    assert blend(2.5, 2.5, 0.6) == 2.5


@pytest.mark.parametrize("alpha", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_alpha_rejected(alpha: float) -> None:
    with pytest.raises(ValueError, match="alpha"):
        blend(3.0, 3.0, alpha)


@pytest.mark.parametrize("model,prior", [(-0.1, 2.0), (5.1, 2.0), (2.0, -0.1), (2.0, 5.1)])
def test_out_of_range_inputs_rejected(model: float, prior: float) -> None:
    with pytest.raises(ValueError, match="range"):
        blend(model, prior, 0.6)


@given(
    model=st.floats(0, 5),
    prior=st.floats(0, 5),
    alpha=st.floats(0, 1),
)
def test_blend_properties(model: float, prior: float, alpha: float) -> None:
    result = blend(model, prior, alpha)
    assert 0 <= result <= 5
    assert (result * 2) == int(result * 2)  # a multiple of 0.5
    low, high = min(model, prior), max(model, prior)
    assert low - 0.25 <= result <= high + 0.25  # a blend never leaves the input span (+rounding)


@pytest.mark.parametrize(
    "confidence,n,expected",
    [
        (0.9, 0, 0.0),
        (0.9, 1, 0.3),
        (0.9, 2, 0.6),
        (0.9, 3, 0.9),
        (0.9, 4, 0.9),
        (0.9, 100, 0.9),
        (0.0, 5, 0.0),
        (1.0, 3, 1.0),
    ],
)
def test_confidence_scales_with_valid_evidence(confidence: float, n: int, expected: float) -> None:
    assert scaled_confidence(confidence, n) == pytest.approx(expected)
