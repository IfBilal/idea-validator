"""The committed ``config/sources.yaml`` and the connectors must agree with each other and with M5."""

import contextlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from pydantic import SecretStr

from svc_scouts.models.runs import Page
from svc_scouts.models.sources import ScoutSources, load_scout_sources
from svc_scouts.services.connectors import CONNECTORS
from svc_scouts.services.context import Credentials
from svc_scouts.services.errors import ScoutError
from svc_scouts.testing import FakeFetcher, make_ctx

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"
RAW = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text())
SOURCES: ScoutSources = load_scout_sources(CONFIG_DIR)
CREDS = Credentials(
    github_token=SecretStr("t"),
    reddit_client_id=SecretStr("i"),
    reddit_client_secret=SecretStr("s"),
    producthunt_token=SecretStr("p"),
)
GENERIC = {
    "hits": [],
    "items": [],
    "access_token": "t",
    "data": {"children": [], "posts": {"edges": [], "pageInfo": {}}},
}


def test_every_connector_has_a_source_entry_and_the_reverse() -> None:
    scout_sources = {n for n in SOURCES.sources if n != "yc"}  # yc is an optional, unbuilt source
    assert scout_sources == set(CONNECTORS)


def test_credential_gated_sources_are_off_and_keyless_ones_are_on() -> None:
    enabled = {n for n, c in SOURCES.sources.items() if c.enabled}
    assert {"hackernews", "github", "stackexchange"} <= enabled
    assert not enabled & {"reddit", "producthunt", "yc"}


def test_lobsters_stays_off_because_its_robots_txt_disallows_crawlers() -> None:
    """Found live on 2026-11-27: `User-agent: *  Disallow: /` and ai-train=no. M5 refuses; so do we."""
    assert not SOURCES.sources["lobsters"].enabled


def test_every_source_host_is_on_the_m5_allow_list() -> None:
    for name, cfg in SOURCES.sources.items():
        assert set(cfg.hosts) <= set(SOURCES.allow_list), name


def test_the_budgets_are_positive_and_explicit_for_every_connector() -> None:
    for name in CONNECTORS:
        raw = RAW["sources"][name]
        assert raw["max_requests_per_run"] >= 1 and raw["max_items_per_run"] >= 1, name


def test_the_source_mix_can_reach_the_data_minimum() -> None:
    """At most this many raw items per run from keyless sources must exceed 1,500 cards."""
    keyless = ("hackernews", "github", "lobsters", "stackexchange")
    assert sum(SOURCES.sources[n].max_items_per_run for n in keyless) >= 1500


@pytest.mark.parametrize("name", sorted(CONNECTORS))
async def test_every_url_a_connector_builds_is_https_on_an_allow_listed_host_of_its_source(
    name: str,
) -> None:
    cfg = SOURCES.sources[name]
    fetcher = FakeFetcher({"https://": Page(status=200, json_=GENERIC)})
    ctx = make_ctx(name, fetcher, options=cfg.options, credentials=CREDS)
    with contextlib.suppress(ScoutError):  # a generic payload does not fit every shape
        await CONNECTORS[name].collect(ctx)
    assert fetcher.calls, name
    for call in fetcher.calls:
        parts = urlsplit(call.url)
        assert parts.scheme == "https" and parts.port is None and not parts.username, call.url
        assert parts.hostname in cfg.hosts, call.url
        assert parts.hostname in SOURCES.allow_list, call.url
        assert parts.fragment == "", call.url


def test_no_secret_value_is_committed_in_the_sources_file() -> None:
    text = (CONFIG_DIR / "sources.yaml").read_text().lower()
    for word in ("token:", "secret:", "password", "api_key", "client_secret"):
        assert word not in text
