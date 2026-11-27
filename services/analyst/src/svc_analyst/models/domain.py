"""Niches, monthly stats, gaps and the analysis snapshot (docs/harness/04 §2)."""

from datetime import date
from typing import Literal

from pydantic import Field

from common.schemas.base import Message
from common.schemas.types import CardId, EvidenceId

NicheStatus = Literal["rising", "flat", "saturated"]


class MonthStat(Message):
    month: date  # first day of the month
    launches: int = Field(ge=0)
    mentions: int = Field(ge=0)


class Niche(Message):
    id: str
    name: str
    size: int = Field(ge=1)
    trend_slope: float  # slope of monthly mentions, last 6 months, normalised by the mean
    activity_slope: float  # same for mentions + launches; drives ``status`` (docs/10 §8)
    status: NicheStatus
    keywords: list[str]
    card_ids: list[CardId]
    series: list[MonthStat]


class Gap(Message):
    id: str
    niche_id: str
    description: str
    evidence_ids: list[EvidenceId] = Field(min_length=1)
    complaint_count: int = Field(ge=1)
    product_coverage: int = Field(ge=0)
    score: float = Field(ge=0)


class Snapshot(Message):
    """Everything one recompute produced."""

    as_of: date | None
    niches: list[Niche]
    gaps: list[Gap]
    unassigned: list[CardId]
