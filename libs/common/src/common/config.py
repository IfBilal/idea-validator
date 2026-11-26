"""Configuration: env/ConfigMap only, never hard-coded hosts (CLAUDE.md rules 10-12)."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_DIR = Path("/etc/idea-validator")


class ConfigError(Exception):
    """A required config file or value is missing or malformed."""


class ServiceSettings(BaseSettings):
    """Common settings every service shares. Subclass and set ``env_prefix`` per service."""

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "service"
    log_level: str = "INFO"
    internal_token: SecretStr | None = None
    config_dir: Path = DEFAULT_CONFIG_DIR


def load_yaml_config(
    name: str, *, config_dir: Path | None = None, env_prefix: str = ""
) -> dict[str, Any]:
    """Load ``<config_dir>/<name>.yaml`` (a mounted ConfigMap); env vars override top-level keys.

    ``VERDICT_T_GO=72`` with ``env_prefix="VERDICT_"`` overrides the key ``t_go``.
    """
    directory = config_dir or Path(os.environ.get("CONFIG_DIR", str(DEFAULT_CONFIG_DIR)))
    path = directory / f"{name}.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    if env_prefix:
        for key in list(data):
            override = os.environ.get(f"{env_prefix}{str(key).upper()}")
            if override is not None:
                data[key] = yaml.safe_load(override)
    return data
