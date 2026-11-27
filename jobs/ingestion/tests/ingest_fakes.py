"""In-memory stand-in for M6 with the behaviour the job relies on (see services/kb)."""

import gzip
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.errors import Unauthorized, UpstreamError, ValidationFailed
from common.ids import canonical_url, card_id_for
from ingestion.models import IngestOutcome


def read_gz_text(path: Path) -> str:
    return gzip.decompress(path.read_bytes()).decode("utf-8")


class FakeKb:
    """Upsert by canonical URL, platforms accumulate, identical content is ``changed=False``."""

    def __init__(self) -> None:
        self.cards: dict[str, dict[str, Any]] = {}  # canonical url -> stored card
        self.platforms: dict[str, set[str]] = {}
        self.calls: list[list[dict[str, Any]]] = []
        self.fail_calls: set[int] = set()  # 1-based call numbers that raise UpstreamError
        self.reject_urls: set[str] = set()  # a batch containing one of these is a 422
        self.unauthorized = False

    async def ingest(self, cards: list[dict[str, Any]]) -> list[IngestOutcome]:
        self.calls.append(cards)
        if self.unauthorized:
            raise Unauthorized("bad token")
        if len(self.calls) in self.fail_calls:
            raise UpstreamError("kb unavailable")
        if len(cards) > 50:
            raise ValidationFailed("batch too large")
        if any(c["url"] in self.reject_urls for c in cards):
            raise ValidationFailed("card rejected")
        out = []
        for card in cards:
            url = canonical_url(card["url"])
            existing = self.cards.get(url)
            created = existing is None
            changed = existing is None or existing["content_hash"] != card["content_hash"]
            if created or changed:
                self.cards[url] = card
            self.platforms.setdefault(url, set()).add(card["source"])
            out.append(IngestOutcome(card_id=card_id_for(url), created=created, changed=changed))
        return out

    async def list_cards(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        now = datetime.now(UTC).isoformat()
        rows = [
            card
            | {
                "id": card_id_for(url),
                "platforms": sorted(self.platforms[url]),
                "first_seen_at": now,
                "last_seen_at": now,
            }
            for url, card in sorted(self.cards.items(), key=lambda kv: card_id_for(kv[0]))
        ]
        return rows[offset : offset + limit], len(rows)
