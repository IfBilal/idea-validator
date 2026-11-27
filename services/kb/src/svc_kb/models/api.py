"""Request and response bodies of the M6 HTTP API (docs/harness/05, issue #29)."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from common.schemas import EvidenceItem
from common.schemas.base import Message
from common.schemas.types import CardId, EvidenceId, Kind, Source
from svc_kb.models.cards import CardOut

MAX_BATCH = 200


class SearchFiltersIn(Message):
    kinds: list[Kind] | None = Field(default=None, max_length=10)
    sources: list[Source] | None = Field(default=None, max_length=10)
    niches: list[str] | None = Field(default=None, max_length=20)
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_niche_alias(cls, data: object) -> object:
        """The issue spells it ``niche``, docs/harness/05 spells it ``niches``: take either."""
        if isinstance(data, dict) and "niche" in data:
            data = dict(data)
            one = data.pop("niche")
            if "niches" not in data and one is not None:
                data["niches"] = [one] if isinstance(one, str) else one
        return data

    @model_validator(mode="after")
    def _ordered_dates(self) -> "SearchFiltersIn":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from is after date_to")
        return self


class SearchRequest(Message):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=10, ge=1, le=100)
    filters: SearchFiltersIn = Field(default_factory=SearchFiltersIn)
    mode: Literal["vector", "keyword", "hybrid"] = "hybrid"
    rerank: bool = True  # only the hybrid mode is reranked: one leg alone has nothing to fuse
    run_id: str | None = Field(default=None, max_length=64)


class SearchResponse(Message):
    items: list[EvidenceItem]
    cache_hit: bool = False


class IdsRequest(Message):
    ids: list[str] = Field(max_length=MAX_BATCH)


class EvidenceFull(Message):
    """One chunk with its card: ``text`` is the whole chunk, ``quote`` a verbatim prefix of it."""

    evidence_id: EvidenceId
    card_id: CardId
    card_name: str
    source: Source
    kind: Kind
    url: str
    text: str
    quote: str
    published_at: date | None = None
    retrieved_at: datetime


class EvidenceBatchResponse(Message):
    items: list[EvidenceFull]
    missing: list[str]


class CardsBatchResponse(Message):
    items: list[CardOut]
    missing: list[str]


class CardsPage(Message):
    items: list[CardOut]
    total: int
