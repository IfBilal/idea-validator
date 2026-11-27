from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M3 settings. URLs and secrets come from env / K8s Secrets, never from code."""

    service_name: str = "orchestrator"
    database_url: SecretStr | None = None  # Postgres checkpointer (schema ``orchestrator``)
    redis_url: SecretStr | None = None  # budget ledger + run events
    question_timeout_s: float = 600
    environment: str = "dev"  # "production" forbids the in-memory checkpointer

    # Other modules, reached over HTTP only. Defaults are the in-cluster service names.
    profile_url: str = "http://profile:8000"
    kb_url: str = "http://kb:8000"
    analyst_url: str = "http://analyst:8000"
    scouts_url: str = "http://scouts:8000"
    validators_url: str = "http://validators:8000"
    verdict_url: str = "http://verdict:8000"
    critic_url: str = "http://critic:8000"
