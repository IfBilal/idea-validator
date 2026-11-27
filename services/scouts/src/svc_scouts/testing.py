"""Test doubles: an in-memory ``RawStore`` and a scripted ``Fetcher`` (no network, no database)."""

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from svc_scouts.models.items import RawItem
from svc_scouts.models.runs import ApiRequest, Page, ScoutState
from svc_scouts.models.sources import SourceCfg
from svc_scouts.services.context import Credentials, ScoutContext


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.states: dict[str, ScoutState] = {}

    async def upsert_items(self, items: Sequence[RawItem]) -> tuple[int, int, int]:
        added = updated = unchanged = 0
        for item in items:
            key = (item.source, item.url)
            row = self.rows.get(key)
            fresh = {"body": item.body(), "hash": item.content_hash, "processed": False}
            if row is None:
                self.rows[key] = fresh
                added += 1
            elif row["hash"] == item.content_hash:
                row["body"] = item.body()
                unchanged += 1
            else:
                self.rows[key] = fresh
                updated += 1
        return added, updated, unchanged

    async def get_state(self, source: str) -> ScoutState:
        return self.states.get(source, ScoutState()).model_copy(deep=True)

    async def save_state(self, source: str, state: ScoutState) -> None:
        self.states[source] = state.model_copy(deep=True)

    async def ping(self) -> bool:
        return True


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def fixture(name: str) -> Any:
    """A recorded API payload from ``tests/fixtures/<name>.json``."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


Route = Page | Exception | Callable[[ApiRequest], Page | Exception]


class FakeFetcher:
    """Answers by URL: exact match first, then the longest registered prefix."""

    def __init__(self, routes: dict[str, Route] | None = None) -> None:
        self.routes: dict[str, Route] = dict(routes or {})
        self.calls: list[ApiRequest] = []

    def add_json(self, url: str, data: Any, status: int = 200, *, flagged: bool = False) -> None:
        self.routes[url] = Page(status=status, json_=data, flagged=flagged)

    async def fetch(self, request: ApiRequest, *, source: str, run_id: str) -> Page:
        self.calls.append(request)
        route = self.routes.get(request.url)
        if route is None:
            matches = [p for p in self.routes if request.url.startswith(p)]
            if not matches:
                return Page(status=404, text="not found")
            route = self.routes[max(matches, key=len)]
        if callable(route):
            route = route(request)
        if isinstance(route, Exception):
            raise route
        return route


NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def make_ctx(
    source: str,
    fetcher: Any,
    *,
    options: dict[str, Any] | None = None,
    cursor: dict[str, Any] | None = None,
    limit: int = 1000,
    max_requests: int = 150,
    credentials: Credentials | None = None,
) -> ScoutContext:
    cfg = SourceCfg.model_validate(
        {"max_requests_per_run": max_requests, "max_items_per_run": 10_000, **(options or {})}
    )
    return ScoutContext(
        source=source,
        cfg=cfg,
        fetcher=fetcher,
        credentials=credentials or Credentials(),
        cursor=dict(cursor or {}),
        run_id="run_test",
        limit=limit,
        now=NOW,
    )
