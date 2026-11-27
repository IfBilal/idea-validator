import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profiles import ProfileUpdate
from .tables import Profile


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(self, user_id: uuid.UUID) -> Profile | None:
        stmt = (
            select(Profile)
            .where(Profile.user_id == user_id)
            .order_by(Profile.version.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, user_id: uuid.UUID, version: int) -> Profile | None:
        return await self._session.get(Profile, (user_id, version))

    async def add_version(self, user_id: uuid.UUID, data: ProfileUpdate) -> Profile:
        """Append the next version. The caller holds the user row lock, so versions never race."""
        current = (
            await self._session.execute(
                select(func.coalesce(func.max(Profile.version), 0)).where(
                    Profile.user_id == user_id
                )
            )
        ).scalar_one()
        row = Profile(user_id=user_id, version=current + 1, **data.model_dump())
        self._session.add(row)
        await self._session.flush()
        return row
