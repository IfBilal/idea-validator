"""Opt-in live check through a real M5: ``SCOUTS_LIVE=1 TOOLS_URL=... INTERNAL_TOKEN=... pytest``.

Skipped by default (CI and ``make test`` never touch the network). It reads one small page from
each keyless source through M5 and checks the connector can parse today's real payload.
"""

import os
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from svc_scouts.clients.tools import ToolsClient
from svc_scouts.models.sources import load_scout_sources
from svc_scouts.services.connectors import CONNECTORS
from svc_scouts.services.context import Credentials, ScoutContext
from svc_scouts.services.errors import BudgetExhausted

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SCOUTS_LIVE") != "1" or not os.environ.get("TOOLS_URL"),
        reason="live test: set SCOUTS_LIVE=1 and TOOLS_URL (M5) to run",
    ),
]


@pytest.mark.parametrize("name", ["hackernews", "github", "lobsters", "stackexchange"])
async def test_a_keyless_source_yields_valid_items_through_m5(name: str) -> None:
    sources = load_scout_sources()
    token = os.environ.get("INTERNAL_TOKEN")
    client = ToolsClient(os.environ["TOOLS_URL"], internal_token=token)
    ctx = ScoutContext(
        source=name,
        cfg=sources.sources[name].model_copy(update={"max_requests_per_run": 3}),
        fetcher=client,
        credentials=Credentials(github_token=SecretStr(os.environ.get("GITHUB_TOKEN", "")) or None),
        cursor={},
        run_id="live_check",
        limit=20,
        now=datetime.now(UTC),
    )
    try:
        await CONNECTORS[name].collect(ctx)
    except BudgetExhausted:
        pass
    finally:
        await client.aclose()
    assert ctx.items, f"{name} returned no valid item"
    assert ctx.dropped <= len(ctx.items), "most payload rows failed to parse: the API changed?"
