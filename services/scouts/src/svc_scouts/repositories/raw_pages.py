"""Postgres access for schema ``scouts``: ``raw_pages`` (read by M6i) and ``scout_state``."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from svc_scouts.models.items import RawItem
from svc_scouts.models.runs import ScoutState

BATCH = 200

_UPSERT = text(
    """
    INSERT INTO scouts.raw_pages (source, url, body, content_hash, status_code, fetched_at)
    VALUES (:source, :url, CAST(:body AS jsonb), :content_hash, 200, now())
    ON CONFLICT (source, url) DO UPDATE SET
        body = EXCLUDED.body,
        fetched_at = now(),
        processed = CASE WHEN scouts.raw_pages.content_hash = EXCLUDED.content_hash
                         THEN scouts.raw_pages.processed ELSE false END,
        content_hash = EXCLUDED.content_hash
"""
)


class PgRawStore:
    """One short transaction per call. Implements ``services.ports.RawStore``."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_items(self, items: Sequence[RawItem]) -> tuple[int, int, int]:
        added = updated = unchanged = 0
        async with self._sessions() as session, session.begin():
            previous = await self._hashes(session, items)
            for item in items:
                key = (item.source, item.url)
                before = previous.get(key)
                await session.execute(
                    _UPSERT,
                    {
                        "source": item.source,
                        "url": item.url,
                        "body": json.dumps(item.body()),
                        "content_hash": item.content_hash,
                    },
                )
                if before is None:
                    added += 1
                elif before == item.content_hash:
                    unchanged += 1
                else:
                    updated += 1
        return added, updated, unchanged

    @staticmethod
    async def _hashes(
        session: AsyncSession, items: Sequence[RawItem]
    ) -> dict[tuple[str, str], str]:
        found: dict[tuple[str, str], str] = {}
        for start in range(0, len(items), BATCH):
            chunk = items[start : start + BATCH]
            rows = await session.execute(
                text(
                    "SELECT source, url, content_hash FROM scouts.raw_pages "
                    "WHERE (source, url) IN "
                    "(SELECT * FROM unnest(CAST(:s AS text[]), CAST(:u AS text[])))"
                ),
                {"s": [i.source for i in chunk], "u": [i.url for i in chunk]},
            )
            found.update({(r[0], r[1]): r[2] for r in rows})
        return found

    async def get_state(self, source: str) -> ScoutState:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT cursor, failures, opened_at, last_run_at, last_status, last_error "
                        "FROM scouts.scout_state WHERE source = :source"
                    ),
                    {"source": source},
                )
            ).first()
        if row is None:
            return ScoutState()
        return ScoutState(
            cursor=row[0] or {},
            failures=row[1],
            opened_at=row[2],
            last_run_at=row[3],
            last_status=row[4],
            last_error=row[5],
        )

    async def save_state(self, source: str, state: ScoutState) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("""
                    INSERT INTO scouts.scout_state
                        (source, cursor, failures, opened_at, last_run_at, last_status, last_error)
                    VALUES (:source, CAST(:cursor AS jsonb), :failures, :opened_at, :last_run_at,
                            :last_status, :last_error)
                    ON CONFLICT (source) DO UPDATE SET
                        cursor = EXCLUDED.cursor, failures = EXCLUDED.failures,
                        opened_at = EXCLUDED.opened_at, last_run_at = EXCLUDED.last_run_at,
                        last_status = EXCLUDED.last_status, last_error = EXCLUDED.last_error
                """),
                {
                    "source": source,
                    "cursor": json.dumps(state.cursor),
                    "failures": state.failures,
                    "opened_at": state.opened_at,
                    "last_run_at": state.last_run_at,
                    "last_status": state.last_status,
                    "last_error": state.last_error,
                },
            )

    @asynccontextmanager
    async def lock(self, source: str) -> AsyncIterator[bool]:
        """A session-level Postgres advisory lock on a dedicated autocommit connection."""
        key = {"k": f"scouts:{source}"}
        async with self._sessions() as session:
            conn = await session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            got = bool(
                (
                    await conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), key)
                ).scalar()
            )
            try:
                yield got
            finally:
                if got:
                    try:
                        await asyncio.shield(
                            conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), key)
                        )
                    except Exception:
                        await asyncio.shield(session.invalidate())  # a dead connection frees it

    async def prune(self, cutoff: datetime) -> int:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                text("DELETE FROM scouts.raw_pages WHERE processed AND fetched_at < :cutoff"),
                {"cutoff": cutoff},
            )
            return int(result.rowcount)  # type: ignore[attr-defined]

    async def ping(self) -> bool:
        async with self._sessions() as session:
            await session.execute(text("SELECT 1"))
        return True
