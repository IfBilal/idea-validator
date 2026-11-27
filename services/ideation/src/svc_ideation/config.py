from pathlib import Path

from pydantic import Field

from common.config import ServiceSettings

MAX_ROUNDS = 3  # docs/harness/07 debate loop and config/budgets.yaml: hard cap, never raised


class Settings(ServiceSettings):
    """M8 settings, all from env / ConfigMap (``TOOLS_URL``, ``KB_URL``, ``ANALYST_URL`` ...)."""

    service_name: str = "ideation"
    tools_url: str | None = None  # M5: the only way out to an LLM provider
    kb_url: str | None = None  # M6
    analyst_url: str | None = None  # M7
    prompts_dir: Path | None = None
    llm_model: str = "hosted-default"
    llm_timeout_s: float = Field(default=60, gt=0)  # budgets.yaml timeouts_s.llm
    kb_timeout_s: float = Field(default=5, gt=0)  # budgets.yaml timeouts_s.kb_search
    max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_tokens: int = Field(default=2000, ge=1)
    max_ideas: int = Field(default=8, ge=1)
    similarity_threshold: float = Field(default=0.6, gt=0, le=1)  # anti-copy Jaccard cut-off
    search_k: int = Field(default=10, ge=1, le=50)
    max_evidence_items: int = Field(default=40, ge=1)
    max_objections_per_idea: int = Field(default=3, ge=1)
    max_items_per_idea: int = Field(default=4, ge=1)
    max_changes: int = Field(default=3, ge=1)
