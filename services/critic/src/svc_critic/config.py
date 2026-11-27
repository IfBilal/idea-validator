from pydantic import Field

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M12 settings, all from env / ConfigMap (``KB_URL``, ``JUDGE_THRESHOLD``, ...)."""

    service_name: str = "critic"
    kb_url: str | None = None  # M6 base URL; no default host on purpose
    kb_timeout_s: float = Field(default=5.0, gt=0)  # budgets.yaml timeouts_s.kb_search
    kb_max_retries: int = Field(default=2, ge=0, le=5)
    judge_threshold: float = Field(default=0.5, ge=0, le=1)  # lexical-overlap support threshold
    max_claims: int = Field(default=50, ge=1)
    max_objections: int = Field(default=5, ge=1)  # accepted objections kept per request
    max_quote_chars: int = Field(default=200, ge=1)
    rail_max_idea_chars: int = Field(default=2000, ge=1)
    rail_max_answer_chars: int = Field(default=1000, ge=1)
    rail_max_niche_chars: int = Field(default=200, ge=1)
    hard_cap_multiplier: float = Field(default=2.0, ge=1)  # approval ceiling = max_cost x this
    max_body_bytes: int = Field(default=262_144, ge=1)
