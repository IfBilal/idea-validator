"""Stratification report: examples per (dimension x level) cell (docs/harness/11 §1.1-1.2)."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from common.schemas import DIMENSIONS

MIN_CELL = 8
LEVELS = range(6)


def stratification_report(examples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Counter[int]] = {d: Counter() for d in DIMENSIONS}
    abstain: Counter[str] = Counter()
    for ex in examples:
        dim = ex["dimension"]
        level = ex["level"]
        if level is None:
            abstain[dim] += 1
        else:
            counts[dim][min(int(level), 5)] += 1
    table = {d: {str(lv): counts[d][lv] for lv in LEVELS} for d in DIMENSIONS}
    empty = [f"{d}@{lv}" for d in DIMENSIONS for lv in LEVELS if counts[d][lv] == 0]
    under = [f"{d}@{lv}" for d in DIMENSIONS for lv in LEVELS if 0 < counts[d][lv] < MIN_CELL]
    return {
        "counts": table,
        "abstain": {d: abstain[d] for d in DIMENSIONS},
        "empty": empty,
        "under_filled": under + empty,
        "min_cell": MIN_CELL,
        "ok": not empty and not under,
    }
