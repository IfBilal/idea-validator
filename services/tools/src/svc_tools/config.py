from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M5 settings. ``REDIS_URL`` and provider keys come from a K8s Secret, never from code."""

    service_name: str = "tools"
    redis_url: SecretStr | None = None
    run_max_pages: int = 60  # the largest per-run page budget in config/budgets.yaml
    run_max_bytes: int = 120_000_000
