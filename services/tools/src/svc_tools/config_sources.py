"""``config/sources.yaml`` (ConfigMap): allow-list, per-source rate limits and cache TTLs."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from common.config import load_yaml_config


class SourceEntry(BaseModel):
    """One source. Scout-specific keys (feeds, topics, schedule...) are allowed and ignored here."""

    model_config = ConfigDict(extra="allow", frozen=True)

    enabled: bool = True
    rate_limit_per_min: int = Field(default=30, ge=0)
    cache_ttl_s: int = Field(default=3600, ge=0)


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    version: str
    user_agent: str
    allow_list: list[str]
    robots_exempt: list[str] = Field(default_factory=list)
    sources: dict[str, SourceEntry] = Field(default_factory=dict)


DEFAULT_SOURCE = SourceEntry()


def parse_sources(data: dict[str, Any]) -> SourcesConfig:
    return SourcesConfig.model_validate(data)


def load_sources(config_dir: Path | None = None) -> SourcesConfig:
    return parse_sources(load_yaml_config("sources", config_dir=config_dir))
