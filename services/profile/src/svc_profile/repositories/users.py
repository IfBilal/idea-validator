"""Database access for users. No business rules live here."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import User


class EmailTaken(Exception):
    """The email is already registered."""


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, email: str, password_hash: str) -> User:
        user = User(email=email, hash=password_hash)
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise EmailTaken(email) from exc
        return user

    async def by_email_for_update(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def lock(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def record_failure(self, user: User, locked_until: datetime | None) -> None:
        user.failed_logins = (user.failed_logins or 0) + 1
        if locked_until is not None:
            user.locked_until = locked_until
            user.failed_logins = 0
        await self._session.flush()

    async def record_success(self, user: User, now: datetime) -> None:
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        await self._session.flush()
