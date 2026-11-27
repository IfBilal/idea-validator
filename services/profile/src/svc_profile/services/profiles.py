"""Versioned profiles: each PUT adds a version and old versions stay readable."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.errors import NotFound
from common.schemas import StudentProfile

from ..models.profiles import ProfileUpdate
from ..repositories.db import transaction
from ..repositories.profiles import ProfileRepository
from ..repositories.tables import Profile
from ..repositories.users import UserRepository


def _to_schema(row: Profile) -> StudentProfile:
    return StudentProfile(
        user_id=str(row.user_id),
        skills=list(row.skills),
        hours_per_week=row.hours_per_week,
        weeks_available=row.weeks_available,
        team_size=row.team_size,
        compute=row.compute,
        goal=row.goal,
        interests=list(row.interests),
        version=row.version,
    )


class ProfileService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, user_id: uuid.UUID, version: int | None = None) -> StudentProfile:
        async with self._sessions() as session:
            repo = ProfileRepository(session)
            row = await (repo.get(user_id, version) if version else repo.latest(user_id))
        if row is None:
            raise NotFound("profile not found", {"user_id": str(user_id), "version": version})
        return _to_schema(row)

    async def put(self, user_id: uuid.UUID, data: ProfileUpdate) -> StudentProfile:
        async with transaction(self._sessions) as session:
            if await UserRepository(session).lock(user_id) is None:
                raise NotFound("user not found", {"user_id": str(user_id)})
            row = await ProfileRepository(session).add_version(user_id, data)
        return _to_schema(row)
