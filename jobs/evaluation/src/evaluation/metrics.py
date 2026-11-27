"""Agreement between the system's verdicts and the human labels (docs/harness/12 §5).

Rows of the confusion matrix are the human verdict, columns the system verdict, in ``LABELS``
order. A *false GO* (system GO, human NO-GO) is the worse error (Guide §10) and is reported on
its own, together with the milder "optimistic GO" (system GO, human PIVOT).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

LABELS: tuple[str, ...] = ("GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE")
_INDEX = {label: i for i, label in enumerate(LABELS)}


def _validate(human: Sequence[str], system: Sequence[str]) -> None:
    if len(human) != len(system):
        raise ValueError("human and system verdicts must have the same length")
    if not human:
        raise ValueError("cannot evaluate an empty set")
    unknown = {v for v in (*human, *system) if v not in _INDEX}
    if unknown:
        raise ValueError(f"unknown verdict label(s): {sorted(unknown)}")


def confusion_matrix(human: Sequence[str], system: Sequence[str]) -> list[list[int]]:
    _validate(human, system)
    matrix = [[0] * len(LABELS) for _ in LABELS]
    for h, s in zip(human, system, strict=True):
        matrix[_INDEX[h]][_INDEX[s]] += 1
    return matrix


def cohen_kappa(human: Sequence[str], system: Sequence[str]) -> float:
    """Chance-corrected agreement. If chance agreement is 1 the answer is 1 or 0."""
    matrix = confusion_matrix(human, system)
    n = len(human)
    observed = sum(matrix[i][i] for i in range(len(LABELS))) / n
    rows = [sum(row) / n for row in matrix]
    cols = [sum(matrix[r][c] for r in range(len(LABELS))) / n for c in range(len(LABELS))]
    expected = sum(r * c for r, c in zip(rows, cols, strict=True))
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


@dataclass(frozen=True)
class EvalReport:
    n: int
    agreement: float
    kappa: float
    confusion_matrix: list[list[int]]
    false_go: int
    false_go_rate: float
    optimistic_go: int
    per_class: dict[str, dict[str, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "agreement": round(self.agreement, 4),
            "kappa": round(self.kappa, 4),
            "labels": list(LABELS),
            "confusion_matrix": self.confusion_matrix,
            "false_go": self.false_go,
            "false_go_rate": round(self.false_go_rate, 4),
            "optimistic_go": self.optimistic_go,
            "per_class": self.per_class,
        }


def build_report(human: Sequence[str], system: Sequence[str]) -> EvalReport:
    matrix = confusion_matrix(human, system)
    n = len(human)
    go, pivot, nogo = _INDEX["GO"], _INDEX["PIVOT"], _INDEX["NO-GO"]
    human_nogo = sum(matrix[nogo])
    per_class: dict[str, dict[str, float]] = {}
    for label, i in _INDEX.items():
        support = sum(matrix[i])
        predicted = sum(matrix[r][i] for r in range(len(LABELS)))
        hit = matrix[i][i]
        per_class[label] = {
            "precision": hit / predicted if predicted else 0.0,
            "recall": hit / support if support else 0.0,
            "support": support,
        }
    return EvalReport(
        n=n,
        agreement=sum(matrix[i][i] for i in range(len(LABELS))) / n,
        kappa=cohen_kappa(human, system),
        confusion_matrix=matrix,
        false_go=matrix[nogo][go],
        false_go_rate=matrix[nogo][go] / human_nogo if human_nogo else 0.0,
        optimistic_go=matrix[pivot][go],
        per_class=per_class,
    )
