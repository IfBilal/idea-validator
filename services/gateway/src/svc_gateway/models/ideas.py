"""Idea history and comparison as shown to a browser (an allow-list of what M2 returns)."""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from .base import Upstream

VerdictLabel = Literal["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"]  # only M11 makes these


class IdeaSummary(Upstream):
    idea_id: str
    title: str
    source: str
    latest_version: int
    latest_verdict: VerdictLabel | None
    score: float | None
    updated_at: datetime


class IdeaPage(Upstream):
    items: list[IdeaSummary]
    next_cursor: str | None


class VersionSummary(Upstream):
    version: int
    text: str
    change_note: str | None
    parent_version: int | None
    created_at: datetime
    verdict: VerdictLabel | None
    score: float | None
    stability: str | None


class VersionCard(VersionSummary):
    """One version with its stored verdict card (written by M11 through M2, never edited here)."""

    idea_id: str
    title: str
    clarifications: dict[str, Any] | None
    card: dict[str, Any] | None

    @model_validator(mode="after")
    def _card_agrees_with_the_row(self) -> Self:
        """Two copies of one verdict must match; if they do not, nothing is shown (no guessing)."""
        if self.card is not None and "verdict" in self.card:
            inner = self.card["verdict"]
            if inner != self.verdict:
                raise ValueError("the stored card disagrees with its verdict")
        return self


class CompareRow(Upstream):
    idea_id: str
    title: str
    version: int
    verdict: VerdictLabel | None
    stability: str | None
    score: float | None
    confidence: float | None
    scores: dict[str, float | None]


class DimensionDelta(Upstream):
    dimension: str
    scores: dict[str, float | None]  # idea_id -> score
    delta: float | None  # highest minus lowest known score; null when fewer than two are known


class Comparison(Upstream):
    ideas: list[CompareRow]
    dimensions: list[DimensionDelta]
    verdicts: dict[str, VerdictLabel | None]  # idea_id -> verdict label, copied from M2


class IdeaCreated(Upstream):
    idea_id: str
    version: int


class RunRecord(Upstream):
    """M2's record of a run, used to decide who owns it."""

    run_id: str
    kind: str
    state: str
    input: dict[str, Any] = Field(default_factory=dict)
