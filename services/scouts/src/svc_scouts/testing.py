"""Test doubles: an in-memory ``RawStore`` and a scripted ``Fetcher`` (no network, no database)."""

import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry

from svc_scouts.models.items import RawItem
from svc_scouts.models.runs import ApiRequest, Page, ScoutState
from svc_scouts.models.sources import ScoutSources, SourceCfg
from svc_scouts.services.context import Credentials, ScoutContext
from svc_scouts.services.metrics import ScoutMetrics
from svc_scouts.services.runner import ScoutRunner


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.states: dict[str, ScoutState] = {}
        self.locked: set[str] = set()
        self.now = NOW  # what ``fetched_at`` records

    async def upsert_items(self, items: Sequence[RawItem]) -> tuple[int, int, int]:
        added = updated = unchanged = 0
        for item in items:
            key = (item.source, item.url)
            row = self.rows.get(key)
            fresh = {
                "body": item.body(),
                "hash": item.content_hash,
                "processed": False,
                "fetched_at": self.now,
            }
            if row is None:
                self.rows[key] = fresh
                added += 1
            elif row["hash"] == item.content_hash:
                row["body"] = item.body()
                row["fetched_at"] = self.now
                unchanged += 1
            else:
                self.rows[key] = fresh
                updated += 1
        return added, updated, unchanged

    async def get_state(self, source: str) -> ScoutState:
        return self.states.get(source, ScoutState()).model_copy(deep=True)

    async def save_state(self, source: str, state: ScoutState) -> None:
        self.states[source] = state.model_copy(deep=True)

    @asynccontextmanager
    async def lock(self, source: str) -> AsyncIterator[bool]:
        if source in self.locked:
            yield False
            return
        self.locked.add(source)
        try:
            yield True
        finally:
            self.locked.discard(source)

    async def prune(self, cutoff: datetime) -> int:
        old = [k for k, r in self.rows.items() if r["processed"] and r["fetched_at"] < cutoff]
        for key in old:
            del self.rows[key]
        return len(old)

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


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def make_runner(
    fetcher: Any,
    *,
    store: MemoryStore | None = None,
    options: dict[str, Any] | None = None,
    clock: Clock | None = None,
    **kw: Any,
) -> ScoutRunner:
    """A runner for the ``hackernews`` source with in-memory parts (shared ``store`` allowed)."""
    cfg = SourceCfg.model_validate(options or {"algolia_tags": ["show_hn"], "feeds": []})
    return ScoutRunner(
        sources=ScoutSources(sources={"hackernews": cfg}),
        store=store or MemoryStore(),
        fetcher=fetcher,
        credentials=Credentials(),
        metrics=ScoutMetrics(CollectorRegistry()),
        clock=clock or Clock(),
        **kw,
    )


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
