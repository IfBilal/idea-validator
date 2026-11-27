"""Database access for ideas, versions, verdicts and their scores. No business rules here."""

import base64
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import DimensionScoreRow, Idea, IdeaVersion, VerdictRow


def encode_cursor(created_at: datetime, idea_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{idea_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    stamp, _, ident = raw.partition("|")
    return datetime.fromisoformat(stamp), uuid.UUID(ident)


class IdeaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_idea(
        self, user_id: uuid.UUID, title: str, source: str, niche_id: str | None, run_id: str | None
    ) -> Idea:
        idea = Idea(
            user_id=user_id, title=title, source=source, niche_id=niche_id, origin_run_id=run_id
        )
        self._session.add(idea)
        await self._session.flush()
        return idea

    async def owned(
        self, idea_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False
    ) -> Idea | None:
        stmt = select(Idea).where(Idea.id == idea_id, Idea.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def next_version(self, idea_id: uuid.UUID) -> int:
        current = (
            await self._session.execute(
                select(func.coalesce(func.max(IdeaVersion.version), 0)).where(
                    IdeaVersion.idea_id == idea_id
                )
            )
        ).scalar_one()
        return int(current) + 1

    async def add_version(
        self,
        idea_id: uuid.UUID,
        version: int,
        text: str,
        change_note: str | None,
        parent_version: int | None,
        clarifications: dict[str, Any] | None,
        card: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> IdeaVersion:
        row = IdeaVersion(
            idea_id=idea_id,
            version=version,
            text=text,
            change_note=change_note,
            parent_version=parent_version,
            clarifications=clarifications,
            card=card,
            idempotency_key=idempotency_key,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def version(self, idea_id: uuid.UUID, version: int) -> IdeaVersion | None:
        return await self._session.get(IdeaVersion, (idea_id, version))

    async def by_key(self, idea_id: uuid.UUID, key: str) -> IdeaVersion | None:
        stmt = select(IdeaVersion).where(
            IdeaVersion.idea_id == idea_id, IdeaVersion.idempotency_key == key
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def versions(self, idea_id: uuid.UUID) -> list[IdeaVersion]:
        stmt = (
            select(IdeaVersion).where(IdeaVersion.idea_id == idea_id).order_by(IdeaVersion.version)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def latest_version(self, idea_id: uuid.UUID) -> IdeaVersion | None:
        stmt = (
            select(IdeaVersion)
            .where(IdeaVersion.idea_id == idea_id)
            .order_by(IdeaVersion.version.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def latest_verdict(self, idea_id: uuid.UUID, version: int) -> VerdictRow | None:
        stmt = (
            select(VerdictRow)
            .where(VerdictRow.idea_id == idea_id, VerdictRow.version == version)
            .order_by(VerdictRow.created_at.desc(), VerdictRow.id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def page(
        self, user_id: uuid.UUID, limit: int, after: tuple[datetime, uuid.UUID] | None
    ) -> list[Idea]:
        stmt = select(Idea).where(Idea.user_id == user_id)
        if after is not None:
            stmt = stmt.where(
                (Idea.created_at < after[0])
                | ((Idea.created_at == after[0]) & (Idea.id < after[1]))
            )
        stmt = stmt.order_by(Idea.created_at.desc(), Idea.id.desc()).limit(limit + 1)
        return list((await self._session.execute(stmt)).scalars())

    async def add_verdict(self, row: VerdictRow, scores: list[DimensionScoreRow]) -> VerdictRow:
        self._session.add(row)
        await self._session.flush()
        for score in scores:
            score.verdict_id = row.id
            self._session.add(score)
        await self._session.flush()
        return row

    async def scores_of(self, verdict_id: uuid.UUID) -> list[DimensionScoreRow]:
        stmt = select(DimensionScoreRow).where(DimensionScoreRow.verdict_id == verdict_id)
        return list((await self._session.execute(stmt)).scalars())
