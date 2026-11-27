"""``config/llm.yaml`` (ConfigMap): model aliases and the provider hosts M5 may call."""

import ipaddress
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from common.config import ConfigError, load_yaml_config

_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _is_plain_hostname(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return _HOSTNAME.fullmatch(value) is not None
    return False  # IP literals are never allowed


class AliasEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)
    host: str
    fallback_usd_per_1k_tokens: float = Field(gt=0)


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    allowed_hosts: frozenset[str] = Field(min_length=1)
    aliases: dict[str, AliasEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _hosts_are_plain_names_and_cover_every_alias(self) -> "LlmConfig":
        for host in self.allowed_hosts:
            if not _is_plain_hostname(host):
                raise ValueError(f"allowed_hosts entry is not a plain host name: {host!r}")
        for name, alias in self.aliases.items():
            if alias.host not in self.allowed_hosts:
                raise ValueError(
                    f"alias {name!r} uses a host that is not allow-listed: {alias.host}"
                )
        return self


def load_llm_config(config_dir: Path | None = None) -> LlmConfig:
    data = load_yaml_config("llm", config_dir=config_dir)
    try:
        return LlmConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid llm.yaml: {exc}") from exc
