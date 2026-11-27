from pydantic import Field, SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M3 settings. URLs and secrets come from env / K8s Secrets, never from code."""

    service_name: str = "orchestrator"
    database_url: SecretStr | None = None  # Postgres checkpointer (schema ``orchestrator``)
    redis_url: SecretStr | None = None  # budget ledger + run events
    question_timeout_s: float = 600
    environment: str = "dev"  # "production" forbids the in-memory checkpointer

    # Restart recovery (docs/harness/07 §1)
    lease_ttl_s: int = Field(default=60, ge=3)  # a crashed replica's runs are adoptable after this
    resume_concurrency: int = Field(default=4, ge=1, le=64)  # recovered runs executing at once
    recovery_interval_s: float = Field(default=30, ge=0)  # sweep for orphaned runs; 0 = once
    recovery_attempts: int = Field(default=5, ge=1, le=20)  # tries per pass while M2 is down
    recovery_attempt_limit: int = Field(
        default=3, ge=1, le=20
    )  # restores of one run before giving up
    finished_ttl_s: float = Field(default=24 * 3600, ge=0)  # finished runs stay readable in memory

    # Other modules, reached over HTTP only. Defaults are the in-cluster service names.
    profile_url: str = "http://profile:8000"
    kb_url: str = "http://kb:8000"
    analyst_url: str = "http://analyst:8000"
    scouts_url: str = "http://scouts:8000"
    validators_url: str = "http://validators:8000"
    verdict_url: str = "http://verdict:8000"
    critic_url: str = "http://critic:8000"
    ideation_url: str = "http://ideation:8000"
