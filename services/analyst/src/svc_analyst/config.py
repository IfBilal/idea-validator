from pydantic import Field

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M7 settings (env / ConfigMap)."""

    service_name: str = "analyst"
    kb_url: str | None = None  # M6 base URL
    kb_timeout_s: float = Field(default=10.0, gt=0)
    kb_max_retries: int = Field(default=2, ge=0, le=5)
    kb_page_size: int = Field(default=500, ge=1, le=5000)
    recompute_on_start: bool = True
    demo_data: bool = False  # run on built-in fixture cards (dev only, until M6 exists)
    max_body_bytes: int = Field(default=65_536, ge=1)
    search_k: int = Field(default=30, ge=1, le=100)
