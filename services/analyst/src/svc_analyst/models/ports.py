"""Seams to the outside: M6 (cards, search) and the niche store. Implementations live elsewhere."""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from .cards import Card, EvidenceHit
from .domain import Snapshot


class CardSource(Protocol):
    """Read access to M6 cards."""

    async def list_cards(self) -> list[Card]:
        """Every card, sorted by id."""
        ...

    async def get_cards(self, ids: Sequence[str]) -> dict[str, Card]:
        """The cards that exist among ``ids`` (unknown ids are simply absent)."""
        ...

    async def snapshot_date(self) -> date | None:
        """The KB snapshot / last ingestion date: the analysis reference date (``as_of``)."""
        ...


class EvidenceSearch(Protocol):
    """M6 ``POST /search``: ranked evidence for a query."""

    async def search(
        self, query: str, *, k: int = 10, kinds: Sequence[str] | None = None
    ) -> list[EvidenceHit]: ...


class NicheStore(Protocol):
    """Where niches, stats and gaps are kept. A Postgres implementation is out of scope here."""

    async def save(self, snapshot: Snapshot) -> None: ...

    async def load(self) -> Snapshot | None: ...
