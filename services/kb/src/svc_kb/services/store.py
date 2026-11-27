"""What the HTTP layer needs from storage, as a port; Postgres and in-memory implementations."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from svc_kb.models.cards import CardIn, CardOut, EvidenceRow, KbStats, UpsertResult
from svc_kb.repositories.cards import MAX_PAGE, CardRepository
from svc_kb.services.chunking import chunk_text

__all__ = ["MAX_PAGE", "PgStore", "Store"]


class Store(Protocol):
    async def get_many(self, ids: Sequence[str]) -> builtins.list[CardOut]: ...
    async def list(self, *, limit: int, offset: int) -> tuple[builtins.list[CardOut], int]: ...
    async def evidence(self, ids: Sequence[str]) -> builtins.list[EvidenceRow]: ...
    async def stats(self) -> KbStats: ...
    async def upsert(self, card: CardIn) -> UpsertResult: ...
    async def ping(self) -> bool: ...


class PgStore:
    """One short read-only session per call."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_many(self, ids: Sequence[str]) -> builtins.list[CardOut]:
        async with self._sessions() as session:
            return await CardRepository(session).get_many(ids)

    async def list(self, *, limit: int, offset: int) -> tuple[builtins.list[CardOut], int]:
        async with self._sessions() as session:
            return await CardRepository(session).list(limit=limit, offset=offset)

    async def evidence(self, ids: Sequence[str]) -> builtins.list[EvidenceRow]:
        async with self._sessions() as session:
            return await CardRepository(session).evidence(ids)

    async def stats(self) -> KbStats:
        async with self._sessions() as session:
            return await CardRepository(session).stats()

    async def upsert(self, card: CardIn) -> UpsertResult:
        async with self._sessions() as session, session.begin():
            return await CardRepository(session).upsert(
                card, chunk_text("card_00000000", card.text)
            )

    async def ping(self) -> bool:
        async with self._sessions() as session:
            await session.execute(text("SELECT 1"))
        return True
