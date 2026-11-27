"""Fakes for tests of M6 and of its callers: deterministic models and an in-memory store."""

from __future__ import annotations

import builtins
import hashlib
import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from common.ids import canonical_url, card_id_for, parse_evidence_id
from svc_kb.models.cards import CardIn, CardOut, EvidenceRow, KbStats, UpsertResult
from svc_kb.services.chunking import chunk_text
from svc_kb.services.search import SparseVec

DIM = 64
WORD = re.compile(r"[a-z0-9]+")


def _h(word: str) -> int:
    return int.from_bytes(hashlib.md5(word.encode(), usedforsecurity=False).digest()[:4], "big")


class FakeModels:
    """Dense = hashed bag of words; sparse = word counts; rerank = shared-word count."""

    async def dense(self, texts: list[str]) -> builtins.list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DIM
            for w in WORD.findall(t.lower()):
                v[_h(w) % DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    async def sparse(self, texts: list[str]) -> builtins.list[SparseVec]:
        out = []
        for t in texts:
            counts: dict[int, float] = {}
            for w in WORD.findall(t.lower()):
                counts[_h(w)] = counts.get(_h(w), 0.0) + 1.0
            out.append(SparseVec(indices=list(counts), values=list(counts.values())))
        return out

    async def rerank(self, query: str, texts: list[str]) -> builtins.list[float]:
        q = set(WORD.findall(query.lower()))
        return [float(len(q & set(WORD.findall(t.lower())))) for t in texts]


class InMemoryStore:
    """``Store`` over a dict of cards; chunk ``n`` of a card is a slice of its text."""

    def __init__(self, cards: Sequence[CardOut], chunks: dict[str, str]) -> None:
        self._cards = {c.id: c for c in sorted(cards, key=lambda c: c.id)}
        self._chunks = chunks  # evidence id -> chunk text

    async def get_many(self, ids: Sequence[str]) -> builtins.list[CardOut]:
        return [self._cards[i] for i in dict.fromkeys(ids) if i in self._cards]

    async def list(self, *, limit: int, offset: int) -> tuple[builtins.list[CardOut], int]:
        rows = list(self._cards.values())
        return rows[offset : offset + limit], len(rows)

    async def evidence(self, ids: Sequence[str]) -> builtins.list[EvidenceRow]:
        out = []
        for eid in dict.fromkeys(ids):
            try:
                card_id, _ = parse_evidence_id(eid)
            except ValueError:
                continue
            card, text = self._cards.get(card_id), self._chunks.get(eid)
            if card and text:
                out.append(
                    EvidenceRow(
                        evidence_id=eid,
                        card_id=card.id,
                        card_name=card.name,
                        source=card.source,
                        kind=card.kind,
                        url=card.url,
                        text=text,
                        published_at=card.published_at,
                    )
                )
        return out

    async def stats(self) -> KbStats:
        by_source: dict[str, int] = {}
        for c in self._cards.values():
            by_source[c.source] = by_source.get(c.source, 0) + 1
        newest = max((c.fetched_at for c in self._cards.values()), default=None)
        return KbStats(
            cards=len(self._cards),
            chunks=len(self._chunks),
            by_source=by_source,
            by_kind={},
            last_ingested_at={},
            fresh_90d_share=0.0,
            snapshot_date=newest,
        )

    async def upsert(self, card: CardIn) -> UpsertResult:
        url = canonical_url(card.url)
        existing = next((c for c in self._cards.values() if c.url == url), None)
        now = datetime.now(UTC)
        if existing is not None:
            changed = existing.content_hash != card.content_hash
            if changed:
                self._store(existing.id, card, url, existing.first_seen_at, now)
            return UpsertResult(card_id=existing.id, created=False, changed=changed)
        card_id = card_id_for(url)
        self._store(card_id, card, url, now, now)
        return UpsertResult(card_id=card_id, created=True, changed=True)

    def _store(self, card_id: str, card: CardIn, url: str, first: datetime, last: datetime) -> None:
        fields = card.model_dump(exclude={"url"})
        self._cards[card_id] = CardOut.model_validate(
            fields
            | {
                "id": card_id,
                "url": url,
                "platforms": [card.source],
                "first_seen_at": first,
                "last_seen_at": last,
            }
        )
        for key in [k for k in self._chunks if k.startswith(f"{card_id}#")]:
            del self._chunks[key]
        for ch in chunk_text(card_id, card.text):
            self._chunks[ch.evidence_id] = ch.text
        self._cards = dict(sorted(self._cards.items()))

    async def ping(self) -> bool:
        return True
