import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.errors import Conflict, Unauthorized, ValidationFailed
from svc_profile.config import Settings
from svc_profile.services.auth import AuthService

pytestmark = pytest.mark.integration
PASSWORD = "correct horse battery"


def service(sessions: async_sessionmaker[AsyncSession], clock=None) -> AuthService:  # type: ignore[no-untyped-def]
    settings = Settings()
    return AuthService(sessions, settings, clock) if clock else AuthService(sessions, settings)


async def test_register_then_login_returns_the_same_user(sessions) -> None:  # type: ignore[no-untyped-def]
    auth = service(sessions)
    user_id = await auth.register("ada@example.com", PASSWORD)
    assert await auth.login("ada@example.com", PASSWORD) == user_id


async def test_the_password_is_stored_as_an_argon2id_hash(sessions) -> None:  # type: ignore[no-untyped-def]
    await service(sessions).register("ada@example.com", PASSWORD)
    async with sessions() as session:
        stored = (await session.execute(text("SELECT hash FROM app.users"))).scalar_one()
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


async def test_emails_are_unique_ignoring_case(sessions) -> None:  # type: ignore[no-untyped-def]
    auth = service(sessions)
    await auth.register("ada@example.com", PASSWORD)
    with pytest.raises(Conflict):
        await auth.register("ADA@Example.com", PASSWORD)


async def test_short_passwords_are_rejected(sessions) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationFailed, match="at least 10"):
        await service(sessions).register("ada@example.com", "short")


async def test_wrong_password_and_unknown_email_look_identical(sessions) -> None:  # type: ignore[no-untyped-def]
    auth = service(sessions)
    await auth.register("ada@example.com", PASSWORD)
    with pytest.raises(Unauthorized) as wrong:
        await auth.login("ada@example.com", "not the password")
    with pytest.raises(Unauthorized) as unknown:
        await auth.login("nobody@example.com", PASSWORD)
    assert wrong.value.message == unknown.value.message


async def test_five_failures_lock_the_account_for_fifteen_minutes(sessions) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    clock = {"now": now}
    auth = service(sessions, lambda: clock["now"])
    user_id = await auth.register("ada@example.com", PASSWORD)
    for _ in range(5):
        with pytest.raises(Unauthorized):
            await auth.login("ada@example.com", "bad password!")
    with pytest.raises(Unauthorized):  # even the right password is refused while locked
        await auth.login("ada@example.com", PASSWORD)
    clock["now"] = now + timedelta(minutes=14)
    with pytest.raises(Unauthorized):
        await auth.login("ada@example.com", PASSWORD)
    clock["now"] = now + timedelta(minutes=16)
    assert await auth.login("ada@example.com", PASSWORD) == user_id


async def test_a_success_resets_the_failure_counter(sessions) -> None:  # type: ignore[no-untyped-def]
    auth = service(sessions)
    await auth.register("ada@example.com", PASSWORD)
    for _ in range(4):
        with pytest.raises(Unauthorized):
            await auth.login("ada@example.com", "bad password!")
    await auth.login("ada@example.com", PASSWORD)
    with pytest.raises(Unauthorized):
        await auth.login("ada@example.com", "bad password!")
    await auth.login("ada@example.com", PASSWORD)  # one failure after a success does not lock


async def test_concurrent_registrations_of_one_email_create_one_account(sessions) -> None:  # type: ignore[no-untyped-def]
    auth = service(sessions)
    results = await asyncio.gather(
        *(auth.register("race@example.com", PASSWORD) for _ in range(4)), return_exceptions=True
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, Conflict) for r in results if isinstance(r, Exception))
