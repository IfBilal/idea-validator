"""Engine outputs (docs/harness/08 §5). Dump with ``exclude_unset=True, by_alias=True``."""

from typing import Any

from pydantic import Field

from common.schemas.base import Message


class Flip(Message):
    change: str
    verdict: str
    score: float | None


class SensitivityResult(Message):
    runs: int
    flipped: int
    flips: list[Flip]
    note: str | None = None


class WhatIfCandidate(Message):
    dimension: str
    field: str | None = None
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    alt: str | None = None
    delta: float | None = None
    new_score: float | None = None
    new_verdict: str
    stable: bool | None = None


class EvaluationResult(Message):
    idea_id: str | None
    version: int | None
    run_id: str | None
    verdict: str
    stability: str
    score: float | None
    confidence: float | None
    gates: dict[str, str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    weakest_dimension: str | None
    reasons: list[str]
    adjusted_scores: dict[str, float | None]
    sensitivity: SensitivityResult
    versions: dict[str, str]
    what_would_make_go: list[WhatIfCandidate] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Stable JSON-ready form (only fields that were set, aliases applied)."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)
