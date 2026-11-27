"""Runs one source: breaker check, connector, store, state, metrics. No HTTP, no SQL here."""

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from common.logging import get_logger
from svc_scouts.models.runs import RunReport, RunStatus, ScoutState
from svc_scouts.models.sources import ScoutSources, SourceCfg

from .breaker import BreakerState, CircuitBreaker
from .connectors import ALIASES, CONNECTORS, Connector
from .context import Credentials, ScoutContext
from .errors import AlreadyRunning, BudgetExhausted, ScoutError, UnknownSource
from .metrics import ScoutMetrics
from .ports import Fetcher, RawStore

log = get_logger("scouts")
MAX_ERROR = 300


def resolve_source(name: str) -> str:
    name = ALIASES.get(name.lower(), name.lower())
    if name not in CONNECTORS:
        raise UnknownSource(f"unknown source {name!r}; known: {', '.join(sorted(CONNECTORS))}")
    return name


class ScoutRunner:
    def __init__(
        self,
        *,
        sources: ScoutSources,
        store: RawStore,
        fetcher: Fetcher,
        credentials: Credentials,
        metrics: ScoutMetrics,
        breaker_threshold: int = 3,
        breaker_cooldown_s: int = 1800,
        default_limit: int = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources = sources
        self._store = store
        self._fetcher = fetcher
        self._credentials = credentials
        self._metrics = metrics
        self._threshold = breaker_threshold
        self._cooldown = breaker_cooldown_s
        self._default_limit = default_limit
        self._clock = clock
        self._running: set[str] = set()

    def is_running(self, source: str) -> bool:
        return resolve_source(source) in self._running

    async def run(
        self, source: str, *, limit: int | None = None, run_id: str | None = None
    ) -> RunReport:
        name = resolve_source(source)
        if name in self._running:
            raise AlreadyRunning(f"{name} is already running")
        self._running.add(name)
        try:
            return await self._run(name, limit, run_id or f"scout_{uuid.uuid4().hex[:12]}")
        finally:
            self._running.discard(name)

    async def _run(self, name: str, limit: int | None, run_id: str) -> RunReport:
        started = time.perf_counter()
        report = RunReport(source=name, run_id=run_id, status="disabled", started_at=self._clock())
        cfg = self._sources.sources.get(name)
        connector = CONNECTORS[name]
        if cfg is None or not cfg.enabled or not connector.credentials_ok(self._credentials):
            return self._finish(report, "disabled", started, "disabled or missing credentials")
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")

        # cross-process single flight: state is read, changed and written under this lock
        async with self._store.lock(name) as acquired:
            if not acquired:
                raise AlreadyRunning(f"{name} is already running")
            return await self._locked(name, cfg, connector, limit, report, started)

    async def _locked(
        self,
        name: str,
        cfg: SourceCfg,
        connector: Connector,
        limit: int | None,
        report: RunReport,
        started: float,
    ) -> RunReport:
        state = await self._store.get_state(name)
        breaker = CircuitBreaker(state, threshold=self._threshold, cooldown_s=self._cooldown)
        now = self._clock()
        if not breaker.allow(now):
            self._metrics.up.labels(name).set(0)
            return self._finish(report, "circuit_open", started, "circuit breaker is open")
        probe = breaker.status(now) is BreakerState.HALF_OPEN

        ctx = ScoutContext(
            source=name,
            cfg=cfg,
            fetcher=self._fetcher,
            credentials=self._credentials,
            cursor=dict(state.cursor),
            run_id=report.run_id,
            limit=limit or self._default_limit,
            now=now,
        )
        error: str | None = None
        try:
            await connector.collect(ctx)
        except BudgetExhausted:
            pass  # a graceful stop at the run's own budget: progress so far is kept
        except ScoutError as exc:
            error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR]
        except Exception as exc:  # a connector bug must not kill the CronJob without a record
            error = f"{type(exc).__name__}: unexpected error"
            log.error("scout_unexpected_error", source=name, exc_info=True)
        except asyncio.CancelledError:
            # a timeout or shutdown is a failed run: count it, keep what was read, then re-raise
            await asyncio.shield(self._persist(name, ctx, state, breaker, now, "cancelled"))
            raise

        counts = await self._persist(name, ctx, state, breaker, now, error)
        status: RunStatus
        if error:
            status = "partial" if ctx.items else "failed"
        elif not ctx.items:
            status = "empty"
        else:
            status = "ok"
        report.requests, report.items, report.dropped = ctx.requests, len(ctx.items), ctx.dropped
        report.added, report.updated, report.unchanged = counts
        report.flagged = ctx.flagged
        if status == "empty":
            log.warning("scout_source_empty", source=name, probe=probe)
        self._metrics.up.labels(name).set(0 if not breaker.allow(self._clock()) else 1)
        return self._finish(report, status, started, error)

    async def _persist(
        self,
        name: str,
        ctx: ScoutContext,
        state: ScoutState,
        breaker: CircuitBreaker,
        now: datetime,
        error: str | None,
    ) -> tuple[int, int, int]:
        counts = (0, 0, 0)
        if ctx.items:
            counts = await self._store.upsert_items(ctx.items)
        if error is None:
            breaker.record_success()
        else:
            breaker.record_failure(self._clock())
        state.cursor = ctx.cursor
        state.last_run_at = now
        state.last_error = error
        state.last_status = (
            "failed"
            if error and not ctx.items
            else "partial"
            if error
            else "empty"
            if not ctx.items
            else "ok"
        )
        await self._store.save_state(name, state)
        return counts

    def _finish(
        self, report: RunReport, status: RunStatus, started: float, error: str | None
    ) -> RunReport:
        report.status, report.error = status, error
        report.duration_s = round(time.perf_counter() - started, 3)
        m, name = self._metrics, report.source
        m.runs.labels(name, status).inc()
        m.duration.labels(name).observe(report.duration_s)
        for outcome, value in (
            ("added", report.added),
            ("updated", report.updated),
            ("unchanged", report.unchanged),
            ("dropped", report.dropped),
        ):
            if value:
                m.items.labels(name, outcome).inc(value)
        if report.requests:
            m.requests.labels(name, "ok" if status in ("ok", "empty") else status).inc(
                report.requests
            )
        if report.flagged:
            m.flagged.labels(name).inc(report.flagged)
        log.info(
            "scout_run",
            source=name,
            run_id=report.run_id,
            status=status,
            requests=report.requests,
            items=report.items,
            added=report.added,
            updated=report.updated,
            dropped=report.dropped,
            flagged=report.flagged,
            error=error,
        )
        return report


__all__ = ["ScoutRunner", "resolve_source"]
