"""Shapes M7 reads from M6 (assumed; see README). Strict, immutable, unknown fields rejected."""

from datetime import date

from pydantic import Field

from common.schemas.base import Message
from common.schemas.types import CardId, EvidenceId, Kind, Source, Stage

COMPLAINT_KINDS: frozenset[str] = frozenset({"complaint", "request"})
PRODUCT_KINDS: frozenset[str] = frozenset({"product", "repo"})


class Card(Message):
    """An M6 card as M7 needs it (a projection of ``schemas/idea_card.schema.json``)."""

    id: CardId
    name: str
    summary: str = ""
    text: str = ""
    customer: str | None = None
    niche_tags: list[str] = Field(default_factory=list)
    stage: Stage = "unknown"
    funding_usd: int | None = Field(default=None, ge=0)
    github_stars: int | None = Field(default=None, ge=0)
    source: Source
    platforms: list[str] = Field(default_factory=list)  # card_sources.platform, one per platform
    kind: Kind | None = None
    published_at: date | None = None

    @property
    def all_platforms(self) -> frozenset[str]:
        return frozenset(self.platforms) | {self.source}

    @property
    def is_complaint(self) -> bool:
        return self.kind in COMPLAINT_KINDS

    @property
    def is_product(self) -> bool:
        return self.kind in PRODUCT_KINDS

    @property
    def document(self) -> str:
        """The text that is vectorised: name, summary, body and tags."""
        return " ".join([self.name, self.summary, self.text, *self.niche_tags])

    @property
    def evidence_id(self) -> str:
        """Chunk 0 of this card (the chunk M7 cites for whole-card evidence)."""
        return f"{self.id}#c0"


class EvidenceHit(Message):
    """One ranked result of M6 ``POST /search``."""

    evidence_id: EvidenceId
    card_id: CardId
    quote: str = ""
    source: Source
    published_at: date | None = None
    score: float | None = None
