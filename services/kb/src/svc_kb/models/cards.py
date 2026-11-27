"""Card models of M6, mirroring ``schemas/idea_card.schema.json`` (rule 3: schemas first).

``CardIn`` is what ingestion hands to M6: it carries no id, because M6 mints it from the canonical
URL (``common.ids.assign_card_id``) so nobody can choose or overwrite an id. ``CardOut`` is what
M6 returns and what M7 reads (plus ``platforms`` for independence counting).
"""

import hashlib
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from common.schemas.base import Message
from common.schemas.types import Kind, Source

Stage = Literal["launched", "seed", "series_a", "series_b_plus", "acquired", "dead", "unknown"]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]

MAX_TEXT_CHARS = 200_000  # the sanitiser caps fetched text at 200k characters
STAGES: tuple[str, ...] = (
    "launched",
    "seed",
    "series_a",
    "series_b_plus",
    "acquired",
    "dead",
    "unknown",
)


def content_hash_of(text: str) -> str:
    """sha256 of the whitespace-normalised, lower-cased text: the exact-duplicate key."""
    normalised = " ".join(text.split()).lower()
    return hashlib.sha256(normalised.encode()).hexdigest()


class CardIn(Message):
    name: str = Field(min_length=1, max_length=300)
    summary: str = Field(max_length=300)
    problem: str | None = Field(default=None, max_length=2000)
    customer: str | None = Field(default=None, max_length=500)
    niche_tags: list[Tag] = Field(default_factory=list, max_length=20)
    stage: Stage = "unknown"
    funding_usd: int | None = Field(default=None, ge=0)
    source: Source
    kind: Kind = "discussion"
    url: str = Field(min_length=8, max_length=2048)
    published_at: date | None = None
    fetched_at: date
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _fill_hash_and_check_dates(self) -> "CardIn":
        if self.published_at is not None and self.published_at > self.fetched_at:
            raise ValueError(
                "published_at is after fetched_at: a scrape date cannot precede the post"
            )
        if self.content_hash is None:
            object.__setattr__(self, "content_hash", content_hash_of(self.text))
        return self


class CardOut(Message):
    id: str = Field(pattern=r"^card_[0-9a-f]{8,12}$")
    name: str
    summary: str
    problem: str | None = None
    customer: str | None = None
    niche_tags: list[str]
    stage: Stage
    funding_usd: int | None = None
    source: Source
    kind: Kind
    url: str
    published_at: date | None = None
    fetched_at: date
    text: str
    content_hash: str
    platforms: list[Source]
    first_seen_at: datetime
    last_seen_at: datetime


class UpsertResult(Message):
    card_id: str
    created: bool
    changed: bool  # False when an existing card was re-seen with identical content


class EvidenceRow(Message):
    evidence_id: str
    card_id: str
    card_name: str
    source: Source
    kind: Kind
    url: str
    text: str
    published_at: date | None = None


class KbStats(Message):
    cards: int
    chunks: int
    by_source: dict[str, int]
    by_kind: dict[str, int]
    last_ingested_at: dict[str, datetime]
    fresh_90d_share: float
    snapshot_date: date | None
