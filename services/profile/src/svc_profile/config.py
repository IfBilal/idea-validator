from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M2 settings. ``DATABASE_URL`` and ``REDIS_URL`` come from a K8s Secret, never from code."""

    service_name: str = "profile"
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    min_password_length: int = 10
