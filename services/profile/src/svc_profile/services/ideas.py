"""Ideas, their versions, verdict history and comparison. Ownership is checked on every read."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.errors import Conflict, NotFound, ValidationFailed

from ..models.storage import (
    CompareRow,
    IdeaCreated,
    IdeaPage,
    IdeaSummary,
    NewIdea,
    NewVersion,
    PutVersion,
    VersionDetail,
    VersionPut,
    VersionSummary,
)
from ..repositories.db import transaction
from ..repositories.ideas import IdeaRepository, decode_cursor, encode_cursor
from ..repositories.tables import VerdictRow
from ..repositories.users import UserRepository

MAX_PAGE = 50


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


class IdeaService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, body: NewIdea) -> IdeaCreated:
        async with transaction(self._sessions) as session:
            if await UserRepository(session).lock(body.user_id) is None:
                raise NotFound("user not found", {"user_id": str(body.user_id)})
            repo = IdeaRepository(session)
            idea = await repo.add_idea(
                body.user_id, body.title, body.source, body.niche_id, body.origin_run_id
            )
            await repo.add_version(idea.id, 1, body.text, None, None, None)
            return IdeaCreated(idea_id=str(idea.id), version=1)

    async def add_version(self, idea_id: uuid.UUID, body: NewVersion) -> IdeaCreated:
        async with transaction(self._sessions) as session:
            repo = IdeaRepository(session)
            if await repo.owned(idea_id, body.user_id, lock=True) is None:
                raise NotFound("idea not found", {"idea_id": str(idea_id)})
            number = await repo.next_version(idea_id)
            if body.parent_version is not None and body.parent_version >= number:
                raise ValidationFailed("parent_version must be an existing version")
            await repo.add_version(
                idea_id,
                number,
                body.text,
                body.change_note,
                body.parent_version,
                body.clarifications,
            )
            return IdeaCreated(idea_id=str(idea_id), version=number)

    async def put_version(self, idea_id: uuid.UUID, version: int, body: PutVersion) -> VersionPut:
        """Create `base_version + 1` with its card; idempotent on the key, serialised per idea."""
        if version != body.base_version + 1:
            raise ValidationFailed("version must equal base_version + 1")
        async with transaction(self._sessions) as session:
            repo = IdeaRepository(session)
            # the row lock makes concurrent writers of one idea take turns
            if await repo.owned(idea_id, body.user_id, lock=True) is None:
                raise NotFound("idea not found", {"idea_id": str(idea_id)})
            existing = await repo.by_key(idea_id, body.idempotency_key)
            if existing is not None:
                same = (
                    existing.version == version
                    and existing.parent_version == body.base_version
                    and existing.text == body.text
                    and existing.change_note == body.change_note
                    and existing.card == body.card
                )
                if not same:
                    raise Conflict("idempotency key reused with different content")
                return VersionPut(idea_id=str(idea_id), version=version, created=False)
            latest = await repo.latest_version(idea_id)
            if latest is None or latest.version != body.base_version:
                raise Conflict(
                    "base_version is not the latest version",
                    {"latest_version": latest.version if latest else None},
                )
            await repo.add_version(
                idea_id,
                version,
                body.text,
                body.change_note,
                body.base_version,
                None,
                card=body.card,
                idempotency_key=body.idempotency_key,
            )
            return VersionPut(idea_id=str(idea_id), version=version, created=True)

    async def page(self, user_id: uuid.UUID, limit: int, cursor: str | None) -> IdeaPage:
        limit = max(1, min(limit, MAX_PAGE))
        try:
            after = decode_cursor(cursor) if cursor else None
        except ValueError as exc:
            raise ValidationFailed("invalid cursor") from exc
        async with self._sessions() as session:
            repo = IdeaRepository(session)
            ideas = await repo.page(user_id, limit, after)
            items: list[IdeaSummary] = []
            # one small query pair per idea; the page is capped at 50
            for idea in ideas[:limit]:
                latest = await repo.latest_version(idea.id)
                assert latest is not None
                verdict = await repo.latest_verdict(idea.id, latest.version)
                stamp = max(latest.created_at, verdict.created_at) if verdict else latest.created_at
                items.append(
                    IdeaSummary(
                        idea_id=str(idea.id),
                        title=idea.title,
                        source=idea.source,
                        latest_version=latest.version,
                        latest_verdict=verdict.verdict if verdict else None,
                        score=_num(verdict.score) if verdict else None,
                        updated_at=stamp,
                    )
                )
        next_cursor = None
        if len(ideas) > limit:
            last = ideas[limit - 1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return IdeaPage(items=items, next_cursor=next_cursor)

    async def versions(self, idea_id: uuid.UUID, user_id: uuid.UUID) -> list[VersionSummary]:
        async with self._sessions() as session:
            repo = IdeaRepository(session)
            if await repo.owned(idea_id, user_id) is None:
                raise NotFound("idea not found", {"idea_id": str(idea_id)})
            out: list[VersionSummary] = []
            for row in await repo.versions(idea_id):
                out.append(_summary(row, await repo.latest_verdict(idea_id, row.version)))
            return out

    async def detail(self, idea_id: uuid.UUID, user_id: uuid.UUID, version: int) -> VersionDetail:
        async with self._sessions() as session:
            repo = IdeaRepository(session)
            idea = await repo.owned(idea_id, user_id)
            row = await repo.version(idea_id, version) if idea else None
            if idea is None or row is None:
                raise NotFound(
                    "idea version not found", {"idea_id": str(idea_id), "version": version}
                )
            verdict = await repo.latest_verdict(idea_id, version)
            base = _summary(row, verdict)
            return VersionDetail(
                **base.model_dump(),
                idea_id=str(idea_id),
                title=idea.title,
                card=row.card,
                clarifications=row.clarifications,
                verdict_payload=verdict.payload if verdict else None,
            )

    async def compare(self, user_id: uuid.UUID, idea_ids: list[uuid.UUID]) -> list[CompareRow]:
        rows: list[CompareRow] = []
        async with self._sessions() as session:
            repo = IdeaRepository(session)
            for idea_id in idea_ids:
                idea = await repo.owned(idea_id, user_id)
                if idea is None:
                    raise NotFound("idea not found", {"idea_id": str(idea_id)})
                latest = await repo.latest_version(idea_id)
                assert latest is not None
                verdict = await repo.latest_verdict(idea_id, latest.version)
                scores: dict[str, float | None] = {}
                if verdict is not None:
                    for s in await repo.scores_of(verdict.id):
                        scores[s.dimension] = _num(s.score)
                rows.append(
                    CompareRow(
                        idea_id=str(idea_id),
                        title=idea.title,
                        version=latest.version,
                        verdict=verdict.verdict if verdict else None,
                        stability=verdict.stability if verdict else None,
                        score=_num(verdict.score) if verdict else None,
                        confidence=_num(verdict.confidence) if verdict else None,
                        scores=scores,
                    )
                )
        return rows


def _summary(row: Any, verdict: VerdictRow | None) -> VersionSummary:
    return VersionSummary(
        version=row.version,
        text=row.text,
        change_note=row.change_note,
        parent_version=row.parent_version,
        created_at=row.created_at,
        verdict=verdict.verdict if verdict else None,
        score=_num(verdict.score) if verdict else None,
        stability=verdict.stability if verdict else None,
    )
