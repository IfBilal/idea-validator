"""The nightly gate (docs/harness/12 §5): compare a run with the stored baseline.

Fails when agreement drops by more than 5 points, when the false-GO count increases, or when any
behaviour check is below 100%. Pure function; ``scripts/check_eval.sh`` wraps it.
"""

from collections.abc import Mapping
from typing import Any

MAX_AGREEMENT_DROP = 0.05
_EPS = 1e-9


def _require(results: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in results:
        raise ValueError(f"{where} results are missing {key!r} (agreement, false_go, behaviour)")
    return results[key]


def check_results(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Return a list of human-readable failures (empty means the gate passes)."""
    base_agreement = float(_require(baseline, "agreement", "baseline"))
    agreement = float(_require(current, "agreement", "current"))
    base_false_go = int(_require(baseline, "false_go", "baseline"))
    false_go = int(_require(current, "false_go", "current"))
    behaviour: Mapping[str, float] = _require(current, "behaviour", "current")
    expected: Mapping[str, float] = _require(baseline, "behaviour", "baseline")

    failures: list[str] = []
    if agreement < base_agreement - MAX_AGREEMENT_DROP - _EPS:
        failures.append(
            f"agreement fell from {base_agreement:.1%} to {agreement:.1%} "
            f"(more than {MAX_AGREEMENT_DROP * 100:.0f} points)"
        )
    if false_go > base_false_go:
        failures.append(f"false GO increased from {base_false_go} to {false_go}")
    for name in sorted(set(behaviour) | set(expected)):
        if name not in behaviour:
            failures.append(f"behaviour check {name!r} is missing from this run")
        elif behaviour[name] < 1.0 - _EPS:
            failures.append(f"behaviour check {name!r} is {behaviour[name]:.0%}, must be 100%")
    return failures
