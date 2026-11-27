"""In-memory M6 stand-ins for tests and for running M7 before M6 exists (``DEMO_DATA=true``)."""

from collections.abc import Sequence

from ..models.cards import Card, EvidenceHit
from ..services.text import TfidfEmbedder, cosine


class InMemoryCardSource:
    def __init__(self, cards: Sequence[Card]) -> None:
        self._cards = sorted(cards, key=lambda c: c.id)
        self._by_id = {c.id: c for c in self._cards}

    async def list_cards(self) -> list[Card]:
        return list(self._cards)

    async def get_cards(self, ids: Sequence[str]) -> dict[str, Card]:
        return {i: self._by_id[i] for i in dict.fromkeys(ids) if i in self._by_id}


class InMemoryEvidenceSearch:
    """TF-IDF cosine ranking over chunk 0 of every card (a stand-in for hybrid search)."""

    def __init__(self, cards: Sequence[Card]) -> None:
        self._cards = sorted(cards, key=lambda c: c.id)
        documents = [c.document for c in self._cards]
        self._embedder = TfidfEmbedder(documents)
        self._vectors = self._embedder.vectorize(documents)

    async def search(
        self, query: str, *, k: int = 10, kinds: Sequence[str] | None = None
    ) -> list[EvidenceHit]:
        if not self._cards:
            return []
        (q,) = self._embedder.vectorize([query])
        ranked = sorted(
            (
                (cosine(q, v), c)
                for c, v in zip(self._cards, self._vectors, strict=True)
                if kinds is None or c.kind in kinds
            ),
            key=lambda pair: (-pair[0], pair[1].id),
        )
        return [
            EvidenceHit(
                evidence_id=c.evidence_id,
                card_id=c.id,
                quote=c.summary[:500],
                source=c.source,
                published_at=c.published_at,
                score=round(score, 6),
            )
            for score, c in ranked[:k]
            if score > 0.0
        ]
