"""The scout view of ``config/sources.yaml`` (the same ConfigMap M5 reads)."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from common.config import load_yaml_config


class SourceCfg(BaseModel):
    """One source. Connector-specific keys (feeds, topics, ...) stay in ``options``."""

    model_config = ConfigDict(extra="allow", frozen=True)

    enabled: bool = True
    hosts: list[str] = Field(default_factory=list)
    max_requests_per_run: int = Field(default=150, ge=1)
    max_items_per_run: int = Field(default=1000, ge=1)

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class ScoutSources(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    allow_list: list[str] = Field(default_factory=list)
    sources: dict[str, SourceCfg] = Field(default_factory=dict)


def load_scout_sources(config_dir: Path | None = None) -> ScoutSources:
    return ScoutSources.model_validate(load_yaml_config("sources", config_dir=config_dir))
