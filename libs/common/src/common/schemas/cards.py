from datetime import date
from typing import Annotated

from pydantic import AwareDatetime, Field

from .base import Message
from .types import CardId, EvidenceId, Kind, Source, Stage

Uri = Annotated[str, Field(json_schema_extra={"format": "uri"})]


class IdeaCard(Message):
    id: CardId
    name: str
    summary: str = Field(max_length=300)
    problem: str | None = None
    customer: str | None = None
    niche_tags: list[str]
    stage: Stage
    funding_usd: int | None = Field(default=None, ge=0)
    source: Source
    url: Uri
    published_at: date | None = None
    fetched_at: date
    text: str
    kind: Kind | None = None
    content_hash: str | None = None


class EvidenceItem(Message):
    evidence_id: EvidenceId
    card_id: CardId
    quote: str = Field(max_length=500)
    source: Source
    url: Uri
    retrieved_at: AwareDatetime
    score: float | None = None
    rank: int | None = None
    card_name: str | None = None
    published_at: date | None = None
    niche: str | None = None


__all__ = ["EvidenceItem", "IdeaCard"]
