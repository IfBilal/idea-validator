"""Registration policy and login (M2 verifies the password; the gateway issues the JWT)."""

from collections.abc import Callable
from datetime import UTC, datetime

from common.errors import ValidationFailed

from ..clients.profile import ProfileClient
from ..config import Settings
from ..models.auth import TokenOut
from ..security import issue_token

COMMON_PASSWORDS = frozenset(
    {
        "password12", "password123", "password1234", "1234567890", "12345678910", "qwertyuiop",
        "qwerty12345", "1q2w3e4r5t", "iloveyou123", "letmein1234", "welcome1234", "admin12345",
        "abcdefghij", "abc1234567", "0123456789", "9876543210", "changeme123", "passw0rd123",
    }
)  # fmt: skip


def check_password_policy(password: str, minimum: int) -> None:
    if len(password) < minimum:
        raise ValidationFailed(f"password must be at least {minimum} characters")
    if password.lower() in COMMON_PASSWORDS:
        raise ValidationFailed("password is too common")


class AuthFlow:
    def __init__(
        self,
        profiles: ProfileClient,
        settings: Settings,
        secret: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._profiles = profiles
        self._settings = settings
        self._secret = secret
        self._clock = clock

    async def register(self, email: str, password: str) -> str:
        check_password_policy(password, self._settings.min_password_length)
        return await self._profiles.register(email, password)

    async def login(self, email: str, password: str) -> TokenOut:
        user_id = await self._profiles.login(email, password)
        token = issue_token(self._secret, user_id, self._settings.jwt_ttl_s, self._clock())
        return TokenOut(access_token=token, expires_in=self._settings.jwt_ttl_s)
