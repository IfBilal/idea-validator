from pathlib import Path

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M10 settings (env / ConfigMap). Hosted mode only: the model server arrives with M10t."""

    service_name: str = "scorer"
    # M5 tool layer base URL; hosted-model calls go to <llm_url>/llm/v1/chat/completions.
    llm_url: str | None = None
    llm_timeout_s: float = 60.0  # config/budgets.yaml timeouts_s.llm
    llm_max_retries: int = 2  # config/budgets.yaml max_retries
    model_name: str = "hosted-default"  # recorded in DimensionScore.model
    rubrics_dir: Path = Path("/app/rubrics")
    prompts_dir: Path = Path("/app/prompts")
