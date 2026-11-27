from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M5 settings. ``REDIS_URL`` and provider keys come from a K8s Secret, never from code."""

    service_name: str = "tools"
    redis_url: SecretStr | None = None
    run_max_pages: int = 60  # the largest per-run page budget in config/budgets.yaml
    run_max_bytes: int = 120_000_000
    llm_run_max_tokens: int = 250_000  # largest per-run token budget in config/budgets.yaml
    llm_run_max_cost_usd: float = 0.25  # largest per-run cost cap in config/budgets.yaml
