"""Drift report (docs/harness/12 section 6): data freshness, coverage and verdict distribution."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from common.schemas.base import Message

VERDICTS = ("GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE")


class SourceFreshness(Message):
    source: str
    cards: int | None  # None when only a snapshot without counts was given
    newest_card: date | None
    newest_age_days: int | None
    basis: Literal["published_at", "last_ingested_at", "none"]
    stale: bool  # no card newer than stale_after_days (or no card at all)


class Freshness(Message):
    stale_after_days: int
    fresh_90d_share: float | None  # M6 /stats: share of dated cards published in the last 90 days
    sources: list[SourceFreshness]


class Coverage(Message):
    basis: Literal["snapshot", "stats_delta", "none"]
    months: dict[str, dict[str, int]]  # source -> "YYYY-MM" -> cards (snapshot basis only)
    new_cards: dict[str, int]  # source -> cards new in the window (see basis)
    zero_new_sources: list[str]
    window_days: int


class Distribution(Message):
    runs: int
    counts: dict[str, int]
    shares: dict[str, float]


class VerdictShift(Message):
    window: int  # N runs per side
    min_runs: int  # each side needs at least this many runs before a shift is judged
    recent: Distribution
    previous: Distribution
    tvd: float | None  # total variation distance, 0 (same) to 1 (disjoint)
    threshold: float
    enough_data: bool
    shifted: bool


class DriftInputs(Message):
    stats: bool
    snapshot: bool
    traces: bool
    previous_report: bool


class DriftReport(Message):
    schema_version: Literal[1] = 1
    generated_at: datetime
    inputs: DriftInputs
    freshness: Freshness
    coverage: Coverage
    verdicts: VerdictShift
    flags: list[str] = Field(default_factory=list)  # machine-readable, e.g. stale:reddit
