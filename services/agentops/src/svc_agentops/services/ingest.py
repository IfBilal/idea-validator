"""POST /traces: validate, clean, de-duplicate, index, append (idempotent on the record id)."""

import asyncio
from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from common.errors import ValidationFailed
from common.logging import get_logger
from common.schemas.trace import TraceRecord

from ..models.traces import IndexRow, IngestResult, Rejection
from .metrics import TraceMetrics
from .ports import TraceFiles, TraceIndex
from .sanitize import RejectedRecord, canonical, record_id, sanitize

log = get_logger("ingest")


class IngestService:
    def __init__(
        self,
        index: TraceIndex,
        files: TraceFiles,
        *,
        max_batch: int,
        max_record_bytes: int,
        metrics: TraceMetrics | None = None,
    ) -> None:
        self._metrics = metrics
        self._index = index
        self._files = files
        self._max_batch = max_batch
        self._max_record_bytes = max_record_bytes

    async def ingest(self, payload: Any) -> IngestResult:
        items = payload if isinstance(payload, list) else [payload]
        if not items:
            raise ValidationFailed("send at least one trace record")
        if len(items) > self._max_batch:
            raise ValidationFailed(
                f"a batch holds at most {self._max_batch} records", {"received": len(items)}
            )
        rejected: list[Rejection] = []
        clean: dict[str, TraceRecord] = {}
        duplicates = 0
        for i, item in enumerate(items):
            try:
                record = sanitize(
                    TraceRecord.model_validate(item), max_bytes=self._max_record_bytes
                )
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
                clean[rid] = record

        by_run: dict[str, list[tuple[str, TraceRecord]]] = defaultdict(list)
        for rid, record in clean.items():
            by_run[record.run_id].append((rid, record))
        accepted = 0
        for run_id, entries in by_run.items():
            new = await self._index.insert_new([_row(rid, r) for rid, r in entries])
            duplicates += len(entries) - len(new)
            fresh = [(rid, r) for rid, r in entries if rid in new]
            try:
                await asyncio.to_thread(
                    self._files.append,
                    run_id,
                    [(rid, r.model_dump(mode="json")) for rid, r in fresh],
                )
            except Exception:
                await self._index.delete(run_id, new)  # nothing half-stored: the client retries
                log.error("trace_append_failed", run_id=run_id, exc_info=True)
                raise
            accepted += len(fresh)
            if self._metrics is not None:
                self._metrics.observe(r for _, r in fresh)
        return IngestResult(accepted=accepted, duplicates=duplicates, rejected=rejected)


def _row(rid: str, r: TraceRecord) -> IndexRow:
    parent = r.parent_step
    return IndexRow(
        run_id=r.run_id,
        record_id=rid,
        ts=r.ts,
        kind=r.kind,
        agent=r.agent,
        service=r.service,
        step_id=r.step_id,
        parent_id=parent,
        status="error" if r.error else "ok",
        cost=r.cost_usd,
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        latency=r.latency_ms,
    )


__all__ = ["IngestService", "canonical"]
