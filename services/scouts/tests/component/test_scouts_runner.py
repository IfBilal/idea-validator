"""The runner end to end with a scripted M5 and an in-memory store (no network, no database)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import CollectorRegistry

from svc_scouts.models.runs import Page, ScoutState
from svc_scouts.models.sources import ScoutSources, SourceCfg
from svc_scouts.services.context import Credentials
from svc_scouts.services.errors import (
    AlreadyRunning,
    FetchFailed,
    SourceRateLimited,
    UnknownSource,
)
from svc_scouts.services.metrics import ScoutMetrics
from svc_scouts.services.runner import ScoutRunner, resolve_source
from svc_scouts.testing import NOW, FakeFetcher, MemoryStore, fixture

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date?tags=show_hn"
HN_CFG = {"enabled": True, "algolia_tags": ["show_hn"], "feeds": []}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def make(
    fetcher: FakeFetcher,
    *,
    store: MemoryStore | None = None,
    hn: dict[str, object] | None = None,
    clock: Clock | None = None,
    creds: Credentials | None = None,
    sources: dict[str, SourceCfg] | None = None,
    **kw: int,
) -> tuple[ScoutRunner, MemoryStore, ScoutMetrics, Clock]:
    store = store or MemoryStore()
    clock = clock or Clock()
    metrics = ScoutMetrics(CollectorRegistry())
    cfg = sources if sources is not None else {"hackernews": SourceCfg.model_validate(hn or HN_CFG)}
    runner = ScoutRunner(
        sources=ScoutSources(sources=cfg),
        store=store,
        fetcher=fetcher,
        credentials=creds or Credentials(),
        metrics=metrics,
        clock=clock,
        **kw,
    )
    return runner, store, metrics, clock


def good() -> FakeFetcher:
    f = FakeFetcher()
    f.add_json(ALGOLIA, fixture("hn_algolia_show"))
    return f


def metric(metrics: ScoutMetrics, name: str, **labels: str) -> float:
    return metrics.registry.get_sample_value(name, labels) or 0.0


async def test_a_good_run_stores_raw_pages_and_reports_counts() -> None:
    runner, store, metrics, _ = make(good())
    report = await runner.run("hackernews")
    assert (report.status, report.items, report.added, report.dropped) == ("ok", 2, 2, 3)
    assert len(store.rows) == 2
    body = store.rows[("hackernews", "https://example.com/invoice")]["body"]
    assert body["title"].startswith("Show HN") and body["platform"] == "hackernews"
    assert metric(metrics, "scout_runs_total", source="hackernews", status="ok") == 1
    assert metric(metrics, "scout_items_total", source="hackernews", outcome="dropped") == 3
    assert metric(metrics, "scout_source_up", source="hackernews") == 1


async def test_rerunning_the_same_data_adds_nothing_and_never_duplicates() -> None:
    runner, store, _, _ = make(good())
    await runner.run("hackernews")
    again = await runner.run("hackernews")
    assert (again.added, again.updated, again.unchanged) == (0, 0, 2)
    assert len(store.rows) == 2


async def test_changed_text_marks_the_row_unprocessed_again() -> None:
    runner, store, _, _ = make(good())
    await runner.run("hackernews")
    for row in store.rows.values():
        row["processed"] = True  # ingestion handled them
    changed = fixture("hn_algolia_show")
    changed["hits"][0]["title"] = "Show HN: Invoice tool v2"
    runner2, _, _, _ = make(FakeFetcher({ALGOLIA: Page(status=200, json_=changed)}), store=store)
    report = await runner2.run("hackernews")
    assert (report.added, report.updated, report.unchanged) == (0, 1, 1)
    assert len(store.rows) == 2  # the url is the key: refreshed, not duplicated
    assert store.rows[("hackernews", "https://example.com/invoice")]["processed"] is False
    assert store.rows[("hackernews", "https://news.ycombinator.com/item?id=41000002")]["processed"]


async def test_an_empty_source_is_reported_empty_not_failed_and_does_not_open_the_breaker() -> None:
    f = FakeFetcher()
    f.add_json(ALGOLIA, {"hits": [], "nbPages": 0})
    runner, store, _, _ = make(f, breaker_threshold=1)
    report = await runner.run("hackernews")
    assert report.status == "empty" and report.items == 0 and report.error is None
    assert (await store.get_state("hackernews")).failures == 0
    assert (await runner.run("hackernews")).status == "empty"  # still allowed


async def test_limit_caps_the_stored_items_and_reports_the_stop_as_ok() -> None:
    runner, store, _, _ = make(good())
    report = await runner.run("hackernews", limit=1)
    assert report.items == 1 and report.status == "ok" and len(store.rows) == 1


async def test_a_failure_with_partial_data_keeps_the_data_and_counts_a_failure() -> None:
    f = good()
    f.routes["https://hn.algolia.com/api/v1/search_by_date?tags=ask_hn"] = Page(status=500)
    runner, store, metrics, _ = make(
        f, hn={"enabled": True, "algolia_tags": ["show_hn", "ask_hn"], "feeds": []}
    )
    report = await runner.run("hackernews")
    assert report.status == "partial" and report.items == 2 and "HTTP 500" in (report.error or "")
    assert len(store.rows) == 2
    state = await store.get_state("hackernews")
    assert state.failures == 1 and state.last_status == "partial"
    # the cursor of the feed that completed moved, the failed one did not
    assert state.cursor["algolia_since"] == {"show_hn": 1760000300}
    assert metric(metrics, "scout_runs_total", source="hackernews", status="partial") == 1


async def test_total_failure_is_status_failed() -> None:
    runner, _, _, _ = make(FakeFetcher({ALGOLIA: Page(status=502)}))
    report = await runner.run("hackernews")
    assert report.status == "failed" and report.items == 0


async def test_rate_limit_stops_without_retrying_and_counts_as_a_failure() -> None:
    f = FakeFetcher({ALGOLIA: SourceRateLimited("slow down", 60)})
    runner, store, _, _ = make(f)
    report = await runner.run("hackernews")
    assert report.status == "failed" and len(f.calls) == 1
    assert (await store.get_state("hackernews")).failures == 1


async def test_the_breaker_opens_after_n_failures_skips_runs_then_probes() -> None:
    f = FakeFetcher({ALGOLIA: Page(status=500)})
    runner, store, metrics, clock = make(f, breaker_threshold=3, breaker_cooldown_s=600)
    for _ in range(3):
        assert (await runner.run("hackernews")).status == "failed"
    calls_before = len(f.calls)
    skipped = await runner.run("hackernews")
    assert skipped.status == "circuit_open" and len(f.calls) == calls_before
    assert metric(metrics, "scout_source_up", source="hackernews") == 0

    clock.now += timedelta(seconds=599)
    assert (await runner.run("hackernews")).status == "circuit_open"
    clock.now += timedelta(seconds=1)  # half-open: one probe
    probe = await runner.run("hackernews")
    assert probe.status == "failed" and len(f.calls) == calls_before + 1
    assert (await runner.run("hackernews")).status == "circuit_open"  # reopened

    clock.now += timedelta(seconds=600)
    f.add_json(ALGOLIA, fixture("hn_algolia_show"))
    assert (await runner.run("hackernews")).status == "ok"  # probe succeeds -> closed
    state = await store.get_state("hackernews")
    assert state.failures == 0 and state.opened_at is None
    assert metric(metrics, "scout_source_up", source="hackernews") == 1


async def test_breaker_state_survives_a_new_process() -> None:
    store = MemoryStore()
    store.states["hackernews"] = ScoutState(failures=3, opened_at=NOW)
    runner, _, _, _ = make(good(), store=store)
    assert (await runner.run("hackernews")).status == "circuit_open"


async def test_a_disabled_source_does_nothing() -> None:
    f = good()
    runner, store, _, _ = make(f, hn={"enabled": False})
    report = await runner.run("hackernews")
    assert report.status == "disabled" and f.calls == [] and store.rows == {}


async def test_a_source_missing_from_the_config_is_disabled() -> None:
    runner, _, _, _ = make(good(), sources={})
    assert (await runner.run("hackernews")).status == "disabled"


async def test_a_credential_gated_source_stays_off_without_credentials() -> None:
    f = FakeFetcher()
    runner, _, _, _ = make(
        f, sources={"reddit": SourceCfg.model_validate({"enabled": True, "subreddits": ["a"]})}
    )
    assert (await runner.run("reddit")).status == "disabled" and f.calls == []


async def test_unknown_sources_and_aliases() -> None:
    runner, _, _, _ = make(good())
    with pytest.raises(UnknownSource):
        await runner.run("myspace")
    assert resolve_source("HN") == "hackernews" and resolve_source("ph") == "producthunt"


@pytest.mark.parametrize("limit", [0, -5])
async def test_a_non_positive_limit_is_refused(limit: int) -> None:
    runner, _, _, _ = make(good())
    with pytest.raises(ValueError):
        await runner.run("hackernews", limit=limit)


async def test_single_flight_a_second_run_of_the_same_source_is_refused() -> None:
    gate = asyncio.Event()

    async def slow(_: object) -> Page:
        await gate.wait()
        return Page(status=200, json_=fixture("hn_algolia_show"))

    class SlowFetcher(FakeFetcher):
        async def fetch(self, request, *, source, run_id):  # type: ignore[no-untyped-def]
            return await slow(request)

    runner, _, _, _ = make(SlowFetcher())
    first = asyncio.create_task(runner.run("hackernews"))
    await asyncio.sleep(0)
    assert runner.is_running("hackernews")
    with pytest.raises(AlreadyRunning):
        await runner.run("hackernews")
    gate.set()
    assert (await first).status == "ok"
    assert not runner.is_running("hackernews")
    assert (await runner.run("hackernews")).status in ("ok",)  # the flag was released


async def test_the_flag_is_released_even_when_the_run_crashes() -> None:
    class Boom(FakeFetcher):
        async def fetch(self, request, *, source, run_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("connector bug")

    runner, store, _, _ = make(Boom())
    report = await runner.run("hackernews")
    assert report.status == "failed" and "unexpected error" in (report.error or "")
    assert "connector bug" not in (report.error or "")  # internals are not echoed
    assert not runner.is_running("hackernews")
    assert (await store.get_state("hackernews")).failures == 1


async def test_hostile_text_is_stored_inertly_flagged_pages_are_counted_and_nothing_extra_is_fetched() -> (
    None
):
    f = FakeFetcher()
    f.add_json(ALGOLIA, fixture("hn_algolia_ask"), flagged=True)
    runner, store, metrics, _ = make(
        f, hn={"enabled": True, "algolia_tags": ["show_hn"], "feeds": []}
    )
    f.routes[ALGOLIA] = Page(status=200, json_=fixture("hn_algolia_ask"), flagged=True)
    report = await runner.run("hackernews")
    assert report.flagged == 1 and len(f.calls) == 1
    assert metric(metrics, "scout_flagged_pages_total", source="hackernews") == 1
    (row,) = store.rows.values()
    assert "Ignore all previous instructions" in row["body"]["text"]


async def test_a_huge_page_of_items_is_bounded_by_the_item_limit() -> None:
    hits = [
        {"objectID": str(i), "title": f"Show HN: t{i}", "created_at_i": 1000 + i, "points": 1}
        for i in range(5000)
    ]
    f = FakeFetcher({ALGOLIA: Page(status=200, json_={"hits": hits, "nbPages": 1})})
    runner, store, _, _ = make(f)
    report = await runner.run("hackernews", limit=250)
    assert report.items == 250 and len(store.rows) == 250


async def test_request_budget_per_source_is_enforced_by_the_runner() -> None:
    f = FakeFetcher()
    hit = {"objectID": "1", "title": "Show HN: t", "created_at_i": 5}
    f.add_json(ALGOLIA, {"hits": [hit], "nbPages": 99})
    runner, _, _, _ = make(f, hn={**HN_CFG, "max_requests_per_run": 4, "algolia_max_pages": 50})
    report = await runner.run("hackernews")
    assert report.requests == 4 and len(f.calls) == 4 and report.status == "ok"


async def test_the_state_records_the_last_run() -> None:
    runner, store, _, clock = make(good())
    await runner.run("hackernews")
    state = await store.get_state("hackernews")
    assert state.last_run_at == clock.now and state.last_status == "ok" and state.last_error is None


async def test_fetch_failures_never_leak_secrets_into_the_stored_error() -> None:
    f = FakeFetcher({ALGOLIA: FetchFailed("M5 unavailable: UpstreamError")})
    runner, store, _, _ = make(f)
    await runner.run("hackernews")
    error = (await store.get_state("hackernews")).last_error or ""
    assert len(error) <= 300 and "Bearer" not in error


def test_now_is_timezone_aware() -> None:
    assert NOW.tzinfo is UTC
