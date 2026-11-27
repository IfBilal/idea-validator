"""Card storage for M6: idempotent upsert keyed on the canonical URL, reads, and corpus stats."""

import builtins
import hashlib
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.ids import ID_LENGTHS, assign_card_id, canonical_url, parse_evidence_id
from svc_kb.models.cards import CardIn, CardOut, EvidenceRow, KbStats, UpsertResult
from svc_kb.repositories.tables import CardRow, CardSourceRow, ChunkRow
from svc_kb.services.chunking import Chunk

MAX_PAGE = 500
_RETRIES = 5


def _sha1(url: str) -> str:
    return hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()


class CardRepository:
    """One instance per session. ``upsert`` must run inside ``session.begin()``."""

    def __init__(self, session: AsyncSession, *, digest: Callable[[str], str] = _sha1) -> None:
        self._s = session
        self._digest = digest

    async def upsert(self, card: CardIn, chunks: Sequence[Chunk]) -> UpsertResult:
        url = canonical_url(card.url)
        for _ in range(_RETRIES):
            existing = (
                await self._s.execute(select(CardRow).where(CardRow.url == url))
            ).scalar_one_or_none()
            if existing is not None:
                return await self._update(existing, card, chunks)
            try:
                async with self._s.begin_nested():
                    return await self._insert(url, card, chunks)
            except IntegrityError:
                continue  # a parallel upsert took the url or the id: look again
        raise RuntimeError(f"could not upsert {url[:80]!r} after {_RETRIES} attempts")

    async def _free_id(self, url: str) -> str:
        full = self._digest(url)
        candidates = [f"card_{full[:n]}" for n in ID_LENGTHS]
        rows = await self._s.execute(
            select(CardRow.id, CardRow.url).where(CardRow.id.in_(candidates))
        )
        owners = dict(rows.tuples().all())
        return assign_card_id(url, owners.get, digest=self._digest)

    async def _insert(self, url: str, card: CardIn, chunks: Sequence[Chunk]) -> UpsertResult:
        card_id = await self._free_id(url)
        values = card.model_dump(exclude={"text", "url"}) | {
            "id": card_id,
            "url": url,
            "text": card.text,
        }
        await self._s.execute(insert(CardRow).values(**values))
        await self._s.execute(insert(CardSourceRow).values(card_id=card_id, source=card.source))
        await self._write_chunks(card_id, chunks)
        return UpsertResult(card_id=card_id, created=True, changed=True)

    async def _update(self, row: CardRow, card: CardIn, chunks: Sequence[Chunk]) -> UpsertResult:
        await self._s.execute(
            insert(CardSourceRow)
            .values(card_id=row.id, source=card.source)
            .on_conflict_do_nothing()
        )
        changed = row.content_hash != card.content_hash
        fields: dict[str, object] = {"last_seen_at": func.now()}
        if changed:
            fields |= card.model_dump(exclude={"url", "source"})
            await self._s.execute(delete(ChunkRow).where(ChunkRow.card_id == row.id))
            await self._write_chunks(row.id, chunks)
        await self._s.execute(update(CardRow).where(CardRow.id == row.id).values(**fields))
        return UpsertResult(card_id=row.id, created=False, changed=changed)

    async def _write_chunks(self, card_id: str, chunks: Sequence[Chunk]) -> None:
        if chunks:  # the caller's chunks may carry a placeholder id: re-key them to the real card
            await self._s.execute(
                insert(ChunkRow),
                [
                    {
                        "id": f"{card_id}#c{c.index}",
                        "card_id": card_id,
                        "idx": c.index,
                        "text": c.text,
                    }
                    for c in chunks
                ],
            )

    async def _out(self, rows: Sequence[CardRow]) -> builtins.list[CardOut]:
        ids = [r.id for r in rows]
        srcs = await self._s.execute(
            select(CardSourceRow.card_id, CardSourceRow.source)
            .where(CardSourceRow.card_id.in_(ids))
            .order_by(CardSourceRow.source)
        )
        platforms: dict[str, list[str]] = {}
        for card_id, source in srcs.tuples():
            platforms.setdefault(card_id, []).append(source)
        return [
            CardOut.model_validate(
                {c.name: getattr(r, c.name) for c in CardRow.__table__.columns if c.name != "tsv"}
                | {"platforms": platforms.get(r.id, [r.source])}
            )
            for r in rows
        ]

    async def get(self, card_id: str) -> CardOut | None:
        got = await self.get_many([card_id])
        return got[0] if got else None

    async def get_many(self, ids: Sequence[str]) -> builtins.list[CardOut]:
        if not ids:
            return []
        rows = (await self._s.execute(select(CardRow).where(CardRow.id.in_(ids)))).scalars().all()
        by_id = {c.id: c for c in await self._out(rows)}
        return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]

    async def list(self, *, limit: int, offset: int) -> tuple[builtins.list[CardOut], int]:
        if not 1 <= limit <= MAX_PAGE:
            raise ValueError(f"limit must be 1..{MAX_PAGE}, got {limit}")
        if offset < 0:
            raise ValueError(f"offset must be 0 or more, got {offset}")
        total = (await self._s.execute(select(func.count()).select_from(CardRow))).scalar_one()
        rows = (
            (
                await self._s.execute(
                    select(CardRow).order_by(CardRow.id).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return await self._out(rows), total

    async def evidence(self, evidence_ids: Sequence[str]) -> builtins.list[EvidenceRow]:
        wanted: list[str] = []
        for value in evidence_ids:
            try:
                parse_evidence_id(value)
            except ValueError:
                continue  # a malformed id names nothing
            wanted.append(value)
        if not wanted:
            return []
        result = await self._s.execute(
            select(ChunkRow.id, ChunkRow.text, CardRow)
            .join(CardRow, CardRow.id == ChunkRow.card_id)
            .where(ChunkRow.id.in_(wanted))
        )
        by_id = {
            eid: EvidenceRow(
                evidence_id=eid,
                card_id=card.id,
                card_name=card.name,
                source=card.source,
                kind=card.kind,
                url=card.url,
                text=chunk_text,
                published_at=card.published_at,
            )
            for eid, chunk_text, card in result.tuples()
        }
        return [by_id[i] for i in dict.fromkeys(wanted) if i in by_id]

    async def stats(self, *, today: date | None = None) -> KbStats:
        today = today or date.today()
        cards = (await self._s.execute(select(func.count()).select_from(CardRow))).scalar_one()
        chunks = (await self._s.execute(select(func.count()).select_from(ChunkRow))).scalar_one()
        by_source = dict(
            (await self._s.execute(select(CardRow.source, func.count()).group_by(CardRow.source)))
            .tuples()
            .all()
        )
        by_kind = dict(
            (await self._s.execute(select(CardRow.kind, func.count()).group_by(CardRow.kind)))
            .tuples()
            .all()
        )
        last = dict(
            (
                await self._s.execute(
                    select(CardRow.source, func.max(CardRow.last_seen_at)).group_by(CardRow.source)
                )
            )
            .tuples()
            .all()
        )
        snapshot = (await self._s.execute(select(func.max(CardRow.fetched_at)))).scalar_one()
        dated, fresh = (
            await self._s.execute(
                select(
                    func.count(CardRow.published_at),
                    func.count().filter(CardRow.published_at >= today - timedelta(days=90)),
                )
            )
        ).one()
        return KbStats(
            cards=cards,
            chunks=chunks,
            by_source=by_source,
            by_kind=by_kind,
            last_ingested_at=last,
            fresh_90d_share=fresh / dated if dated else 0.0,
            snapshot_date=snapshot,
        )
