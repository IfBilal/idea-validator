"""Registration and login rules: password policy, argon2id, lockout after repeated failures."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.errors import Conflict, Unauthorized, ValidationFailed

from ..config import Settings
from ..repositories.db import transaction
from ..repositories.users import EmailTaken, UserRepository
from .passwords import burn_time, hash_password, verify_password

INVALID = "invalid email or password"  # one message for every failure: no account enumeration


class AuthService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._clock = clock

    async def register(self, email: str, password: str) -> uuid.UUID:
        if len(password) < self._settings.min_password_length:
            raise ValidationFailed(
                f"password must be at least {self._settings.min_password_length} characters"
            )
        try:
            async with transaction(self._sessions) as session:
                user = await UserRepository(session).add(email, hash_password(password))
                return user.id
        except EmailTaken as exc:
            raise Conflict("email is already registered") from exc

    async def login(self, email: str, password: str) -> uuid.UUID:
        """Return the user id or raise ``Unauthorized`` (same message for every failure)."""
        now = self._clock()
        async with transaction(self._sessions) as session:
            repo = UserRepository(session)
            user = await repo.by_email_for_update(email)
            if user is None:
                burn_time()
                raise Unauthorized(INVALID)
            if user.locked_until is not None and user.locked_until > now:
                burn_time()
                raise Unauthorized(INVALID)
            if not verify_password(user.hash, password):
                lock = None
                if (user.failed_logins or 0) + 1 >= self._settings.max_failed_logins:
                    lock = now + timedelta(minutes=self._settings.lockout_minutes)
                await repo.record_failure(user, lock)
                failed = True
            else:
                await repo.record_success(user, now)
                failed = False
            user_id = user.id
        if failed:
            raise Unauthorized(INVALID)
        return user_id
