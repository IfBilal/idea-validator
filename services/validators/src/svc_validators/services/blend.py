"""Score blend (ADR-006): ``final = alpha*model + (1-alpha)*prior`` rounded to 0.5.

Decimal arithmetic keeps the .25/.75 rounding edges exact (no float noise); ties round up.
"""

import math
from decimal import ROUND_FLOOR, Decimal

_HALF = Decimal("0.5")
_MIN, _MAX = Decimal(0), Decimal(5)
EVIDENCE_FOR_FULL_CONFIDENCE = 3  # docs/harness/09 §5: confidence x min(1, n_valid_evidence/3)


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def round_half(value: float | Decimal) -> float:
    """Round to the nearest 0.5 (ties up) and clamp to the 0..5 scale."""
    x = value if isinstance(value, Decimal) else _dec(value)
    rounded = ((x * 2 + _HALF).to_integral_value(rounding=ROUND_FLOOR)) / 2
    return float(min(max(rounded, _MIN), _MAX))


def blend(model: float, prior: float, alpha: float) -> float:
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError(f"alpha must be within [0, 1], got {alpha}")
    for name, value in (("model", model), ("prior", prior)):
        if not math.isfinite(value) or not 0 <= value <= 5:
            raise ValueError(f"{name} score out of range [0, 5]: {value}")
    a = _dec(alpha)
    return round_half(a * _dec(model) + (1 - a) * _dec(prior))


def scaled_confidence(model_confidence: float, n_valid_evidence: int) -> float:
    return model_confidence * min(1.0, n_valid_evidence / EVIDENCE_FOR_FULL_CONFIDENCE)
