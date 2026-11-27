"""Deterministic feature prior from a rubric's ``feature_prior`` rules (first match wins)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..models.rubric import Rubric
from .expr import Compiled, ExprError, compile_expr, evaluate


@dataclass(frozen=True)
class PriorResult:
    """``score`` is None when a needed feature is missing or unusable: unknown, not zero."""

    score: float | None
    rule_index: int | None = None
    problem: str = ""


class PriorCalculator:
    def __init__(self, rubric: Rubric) -> None:
        self._rules: list[tuple[Compiled, float]] = [
            (compile_expr(rule.when), rule.score) for rule in rubric.feature_prior
        ]

    def compute(self, features: Mapping[str, Any]) -> PriorResult:
        for index, (condition, score) in enumerate(self._rules):
            try:
                matched = evaluate(condition, features)
            except ExprError as exc:
                return PriorResult(None, None, str(exc))
            if matched:
                return PriorResult(score, index)
        return PriorResult(None, None, "no prior rule matched")  # pragma: no cover (loader forbids)
