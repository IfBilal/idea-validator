"""The committed ``config/sources.yaml`` must be valid and safe."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from svc_tools.config_sources import parse_sources

CONFIG = Path(__file__).resolve().parents[4] / "config" / "sources.yaml"


def committed() -> dict:  # type: ignore[type-arg]
    data: dict = yaml.safe_load(CONFIG.read_text())  # type: ignore[type-arg]
    return data


def test_the_committed_config_parses() -> None:
    sources = parse_sources(committed())
    assert "api.github.com" in sources.allow_list
    assert sources.user_agent.startswith("IdeaValidatorBot/")


def test_every_allow_listed_host_is_a_plain_lowercase_hostname() -> None:
    for host in parse_sources(committed()).allow_list:
        assert host == host.lower()
        assert "/" not in host and ":" not in host and " " not in host
        assert not host.replace(".", "").isdigit()  # no IP addresses


def test_robots_exempt_hosts_are_all_on_the_allow_list_and_are_api_hosts() -> None:
    sources = parse_sources(committed())
    assert set(sources.robots_exempt) <= set(sources.allow_list)
    assert "www.reddit.com" not in sources.robots_exempt  # a normal website: robots.txt applies


def test_enabled_sources_have_positive_rate_limits_and_ttls() -> None:
    for name, entry in parse_sources(committed()).sources.items():
        if entry.enabled:
            assert entry.rate_limit_per_min > 0, name
            assert entry.cache_ttl_s > 0, name


@pytest.mark.parametrize("missing", ["version", "user_agent", "allow_list"])
def test_required_keys_are_required(missing: str) -> None:
    data = committed()
    del data[missing]
    with pytest.raises(ValidationError):
        parse_sources(data)


def test_negative_limits_are_rejected() -> None:
    data = committed()
    data["sources"]["github"]["rate_limit_per_min"] = -1
    with pytest.raises(ValidationError):
        parse_sources(data)
