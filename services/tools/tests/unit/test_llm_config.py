"""config/llm.yaml: aliases, allow-listed provider hosts, validation at start-up."""

from pathlib import Path

import pytest
import yaml

from common.config import ConfigError
from svc_tools.config_llm import load_llm_config

REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"


def write(tmp_path: Path, data: object) -> Path:
    (tmp_path / "llm.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


GOOD = {
    "version": "l1",
    "allowed_hosts": ["api.provider.test"],
    "aliases": {
        "hosted-default": {
            "model": "prov/small",
            "host": "api.provider.test",
            "fallback_usd_per_1k_tokens": 0.01,
        }
    },
}


def test_the_committed_config_loads_and_every_alias_host_is_allow_listed() -> None:
    cfg = load_llm_config(REPO_CONFIG)
    assert "hosted-default" in cfg.aliases
    assert all(a.host in cfg.allowed_hosts for a in cfg.aliases.values())


def test_a_good_config_loads(tmp_path: Path) -> None:
    cfg = load_llm_config(write(tmp_path, GOOD))
    assert cfg.aliases["hosted-default"].model == "prov/small"
    assert cfg.allowed_hosts == frozenset({"api.provider.test"})


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "aliases": {}},
        {**GOOD, "allowed_hosts": []},
        {
            **GOOD,
            "aliases": {
                "hosted-default": {
                    "model": "m",
                    "host": "evil.example",
                    "fallback_usd_per_1k_tokens": 0.01,
                }
            },
        },
        {
            **GOOD,
            "aliases": {
                "hosted-default": {
                    "model": "m",
                    "host": "1.2.3.4",
                    "fallback_usd_per_1k_tokens": 0.01,
                }
            },
        },
        {**GOOD, "allowed_hosts": ["http://api.provider.test"]},
        {**GOOD, "allowed_hosts": ["*.provider.test"]},
        {
            **GOOD,
            "aliases": {
                "x": {"model": "", "host": "api.provider.test", "fallback_usd_per_1k_tokens": 0.01}
            },
        },
        {**GOOD, "extra": 1},
        {**GOOD, "aliases": {"x": {"model": "m", "host": "api.provider.test"}}},
        {
            **GOOD,
            "aliases": {
                "x": {"model": "m", "host": "api.provider.test", "fallback_usd_per_1k_tokens": 0}
            },
        },
        {
            **GOOD,
            "aliases": {
                "x": {"model": "m", "host": "api.provider.test", "fallback_usd_per_1k_tokens": -1}
            },
        },
    ],
)
def test_a_bad_config_stops_the_service_at_start_up(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ConfigError):
        load_llm_config(write(tmp_path, bad))


def test_a_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_llm_config(tmp_path)
