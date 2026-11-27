"""Input and output shapes of the ingestion job.

``RawItem`` is what the scouts (M4) expose: ``{source, url, title, text, created_at, score/stars,
platform, metadata}``. It is deliberately tolerant of extra fields (the scouts may add some) but
strict about the ones we use, so a malformed row is rejected with a reason instead of crashing the
run. ``CardDraft`` mirrors the wire shape of M6 ``POST /ingest`` (``svc_kb.models.cards.CardIn``);
a contract test keeps the two in step.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from common.schemas.types import Kind, Source

Stage = Literal["launched", "seed", "series_a", "series_b_plus", "acquired", "dead", "unknown"]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]

MAX_TEXT_CHARS = 200_000  # M6 refuses a card text longer than this

# names the scouts may use for a feed of one of our sources
SOURCE_ALIASES: dict[str, Source] = {
    "hackernews": "hackernews",
    "hacker_news": "hackernews",
    "hacker-news": "hackernews",
    "hn": "hackernews",
    "hn_show": "hackernews",
    "hn_ask": "hackernews",
    "hn-show": "hackernews",
    "hn-ask": "hackernews",
    "show_hn": "hackernews",
    "ask_hn": "hackernews",
    "github": "github",
    "gh": "github",
    "reddit": "reddit",
    "producthunt": "producthunt",
    "product_hunt": "producthunt",
    "product-hunt": "producthunt",
    "ph": "producthunt",
    "yc": "yc",
    "ycombinator": "yc",
    "lobsters": "lobsters",
    "stackexchange": "stackexchange",
    "stackoverflow": "stackexchange",
    "stack_exchange": "stackexchange",
}


def resolve_source(value: str) -> Source:
    """The canonical source for a scout's feed name; ``ValueError`` for an unknown one."""
    key = value.strip().lower()
    try:
        return SOURCE_ALIASES[key]
    except KeyError:
        raise ValueError(f"unknown source {value[:40]!r}") from None


class RawItem(BaseModel):
    """One raw scout item. Unknown extra fields are ignored; ``source`` and ``url`` are required."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    source: Source
    url: str = Field(min_length=1)
    title: str = ""
    text: str = ""
    created_at: datetime | date | None = None
    fetched_at: datetime | date | None = None
    score: int | None = None
    stars: int | None = None
    platform: Source | None = None  # where it was seen, if different from the mapper's source
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", "platform", mode="before")
    @classmethod
    def _alias(cls, value: object) -> object:
        if isinstance(value, str):
            return resolve_source(value)
        return value

    @field_validator("title", "text", mode="before")
    @classmethod
    def _none_is_empty(cls, value: object) -> object:
        return "" if value is None else value

    @property
    def seen_on(self) -> Source:
        return self.platform or self.source

    @property
    def popularity(self) -> int:
        return max(self.score or 0, self.stars or 0)


class CardDraft(BaseModel):
    """The wire shape M6 accepts (``CardIn``): no id, M6 mints it from the canonical URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

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
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PlannedCard(BaseModel):
    """A card that survived dedupe and every platform that mentioned it (independence evidence)."""

    model_config = ConfigDict(frozen=True)

    card: CardDraft
    platforms: tuple[Source, ...]
    merged: int = 0  # how many other raw items were folded into this card


class Rejection(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    reason: str
    url: str = ""


class IngestOutcome(BaseModel):
    """What M6 said about one posted card."""

    model_config = ConfigDict(frozen=True)

    card_id: str
    created: bool
    changed: bool


class KbClient(Protocol):
    """The part of M6 the job uses. The real one is HTTP; tests inject a fake."""

    async def ingest(self, cards: list[dict[str, Any]]) -> list[IngestOutcome]: ...

    async def list_cards(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]: ...
