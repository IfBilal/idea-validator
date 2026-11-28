"""POST /traces: validate, clean, de-duplicate, append to the file, then index.

Order matters. The JSONL file is the source of truth and readers de-duplicate by record id, so the
file is written FIRST and nothing is ever deleted again: a crash or a failed index insert leaves a
line without an index row (``python -m agentops.reindex`` restores it) and a retry only adds a
duplicate line, never loses a record. The old index-first order needed a compensating delete that
could remove a record a concurrent retry had just been told was a duplicate.
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from common.errors import DomainError, ErrorCode, ValidationFailed
from common.logging import get_logger
from common.schemas.trace import TraceRecord

from ..models.traces import IndexRow, IngestResult, Rejection
from ..repositories.files import RunFull, UnsafeRunId
from .metrics import TraceMetrics
from .ports import TraceFiles, TraceIndex
from .sanitize import RejectedRecord, privileged_name, record_id, sanitize

log = get_logger("ingest")
OWNER_ID_MAX = 128


class StorageFull(DomainError):
    """The trace volume is nearly full: refuse instead of corrupting a file (507)."""

    code, status_code = ErrorCode.INTERNAL, 507


@dataclass(frozen=True)
class _Item:
    index: int
    record_id: str
    record: TraceRecord


class IngestService:
    def __init__(
        self,
        index: TraceIndex,
        files: TraceFiles,
        *,
        max_batch: int,
        max_record_bytes: int,
        metrics: TraceMetrics | None = None,
        max_run_bytes: int = 8 * 1024 * 1024,
        max_run_records: int = 5000,
        min_free_bytes: int = 0,
        max_future: timedelta = timedelta(days=1),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._max_future = max_future
        self._clock = clock
        self._metrics = metrics
        self._index = index
        self._files = files
        self._max_batch = max_batch
        self._max_record_bytes = max_record_bytes
        self._max_run_bytes = max_run_bytes
        self._max_run_records = max_run_records
        self._min_free_bytes = min_free_bytes

    def _clean(self, item: Any) -> TraceRecord:
        record = sanitize(TraceRecord.model_validate(item), max_bytes=self._max_record_bytes)
        if record.ts > self._clock() + self._max_future:
            # a far-future ts would keep a run out of retention for good
            raise RejectedRecord("ts is more than a day in the future")
        return record

    async def ingest(self, payload: Any) -> IngestResult:
        items = payload if isinstance(payload, list) else [payload]
        if not items:
            raise ValidationFailed("send at least one trace record")
        if len(items) > self._max_batch:
            raise ValidationFailed(
                f"a batch holds at most {self._max_batch} records", {"received": len(items)}
            )
        if self._files.free_bytes() < self._min_free_bytes:
            raise StorageFull("the trace volume is almost full")
        rejected: list[Rejection] = []
        clean: dict[str, _Item] = {}
        duplicates = 0
        for i, item in enumerate(items):
            try:
                record = await asyncio.to_thread(self._clean, item)  # regex work off the loop
            except ValidationError as exc:
                first = exc.errors(include_input=False, include_url=False)[0]
                rejected.append(
                    Rejection(index=i, reason=f"{'.'.join(map(str, first['loc']))}: {first['msg']}")
                )
                continue
            except RejectedRecord as exc:
                rejected.append(Rejection(index=i, reason=str(exc)))
                continue
            rid = record_id(record)
            if rid in clean:
                duplicates += 1
            else:
                clean[rid] = _Item(i, rid, record)

        by_run: dict[str, list[_Item]] = defaultdict(list)
        for entry in clean.values():
            by_run[entry.record.run_id].append(entry)
        accepted = 0
        for run_id, entries in by_run.items():
            known = await self._index.existing(run_id, [e.record_id for e in entries])
            duplicates += len(known)
            for e in entries:  # a retry must repair an owner a failed attempt did not store
                if e.record_id in known:
                    await self._record_owner(e.record)
            fresh = [e for e in entries if e.record_id not in known]
            if not fresh:
                continue
            reason = await self._append(run_id, fresh)
            if reason is not None:
                rejected += [Rejection(index=e.index, reason=reason) for e in fresh]
                continue
            new = await self._index.insert_new([_row(e) for e in fresh])
            duplicates += len(fresh) - len(new)
            accepted += len(new)
            for e in fresh:
                await self._record_owner(e.record)
            if self._metrics is not None:
                self._metrics.observe(e.record for e in fresh if e.record_id in new)
        rejected.sort(key=lambda r: r.index)
        return IngestResult(accepted=accepted, duplicates=duplicates, rejected=rejected)

    async def _append(self, run_id: str, fresh: list[_Item]) -> str | None:
        """Append to the run's file; a reason string if the run is full, else None.

        Both caps are checked inside the file lock, so concurrent batches cannot overshoot them.
        """
        lines = [(e.record_id, e.record.model_dump(mode="json")) for e in fresh]
        try:
            await asyncio.to_thread(
                self._files.append, run_id, lines, self._max_run_bytes, self._max_run_records
            )
        except UnsafeRunId:
            return "the run's trace file is not a regular file"
        except RunFull as exc:
            limit = self._max_run_records if exc.what == "records" else self._max_run_bytes
            return f"run {run_id} reached its limit of {limit} {exc.what}"
        except OSError as exc:
            log.error("trace_append_failed", run_id=run_id, error=type(exc).__name__)
            raise StorageFull("the trace volume could not be written") from exc
        return None

    async def _record_owner(self, record: TraceRecord) -> None:
        if privileged_name(record) != "run_owner":
            return
        user = record.output.get("user_id") if isinstance(record.output, dict) else None
        if isinstance(user, str) and 0 < len(user) <= OWNER_ID_MAX:
            await self._index.set_owner(record.run_id, user)


def _row(e: _Item) -> IndexRow:
    return index_row(e.record_id, e.record)


def index_row(rid: str, r: TraceRecord) -> IndexRow:
    return IndexRow(
        run_id=r.run_id,
        record_id=rid,
        ts=r.ts,
        kind=r.kind,
        agent=r.agent,
        service=r.service,
        step_id=r.step_id,
        parent_id=r.parent_step,
        status="error" if r.error else "ok",
        cost=r.cost_usd,
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        latency=r.latency_ms,
    )
