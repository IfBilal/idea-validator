"""``config/llm.yaml``: model aliases. Validated against the fetch allow-list at start-up."""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from common.config import load_yaml_config

_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,63}")


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=40)
    base_url: str
    model: str = Field(min_length=1, max_length=100)
    api_key_env: str = Field(pattern=_ENV_NAME.pattern)
    price_in_per_mtok: float = Field(ge=0, le=1000)
    price_out_per_mtok: float = Field(ge=0, le=1000)
    timeout_s: float = Field(default=30.0, gt=0, le=120)


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    version: str = "l1"
    aliases: dict[str, Alias]


def parse_llm_config(data: dict[str, Any], allow_list: Iterable[str]) -> LlmConfig:
    config = LlmConfig.model_validate(data)
    allowed = {h.strip().lower().rstrip(".") for h in allow_list}
    for name, alias in config.aliases.items():
        parts = urlsplit(alias.base_url)
        if parts.scheme != "https" or parts.username or parts.password or "@" in parts.netloc:
            raise ValueError(f"alias {name}: base_url must be plain https without credentials")
        if parts.port not in (None, 443):
            raise ValueError(f"alias {name}: only port 443 is allowed")
        host = (parts.hostname or "").lower().rstrip(".")
        if host not in allowed:
            raise ValueError(f"alias {name}: host {host!r} is not on the allow-list")
    return config


def load_llm_config(allow_list: Iterable[str], config_dir: Path | None = None) -> LlmConfig:
    return parse_llm_config(load_yaml_config("llm", config_dir=config_dir), allow_list)
