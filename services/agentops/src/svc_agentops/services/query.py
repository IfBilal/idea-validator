"""GET /runs/{id}/trace and GET /runs."""

import asyncio
import base64
import binascii
from datetime import datetime

from common.errors import NotFound, ValidationFailed
from common.schemas.trace import TraceRecord

from ..models.traces import RunList, TraceFlat, TraceTree
from .ports import TraceFiles, TraceIndex
from .sanitize import RUN_ID
from .tree import Entry, build_tree, ordered, totals


def encode_cursor(ts: datetime, run_id: str) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{run_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts_text, run_id = raw.split("|", 1)
        ts = datetime.fromisoformat(ts_text)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("after is not a valid cursor") from exc
    if ts.tzinfo is None or not RUN_ID.fullmatch(run_id):
        raise ValidationFailed("after is not a valid cursor")
    return ts, run_id


class QueryService:
    def __init__(
        self,
        index: TraceIndex,
        files: TraceFiles,
        *,
        max_run_records: int,
        default_limit: int,
        max_limit: int,
    ) -> None:
        self._index = index
        self._files = files
        self._max_records = max_run_records
        self._default_limit = default_limit
        self._max_limit = max_limit

    async def _load(self, run_id: str) -> tuple[list[Entry], bool]:
        if not RUN_ID.fullmatch(run_id):
            raise ValidationFailed("run_id must match [A-Za-z0-9_-]{1,64}")
        rows, truncated = await asyncio.to_thread(self._files.read, run_id, self._max_records)
        if not rows:
            raise NotFound(f"no trace for run {run_id}")
        entries: list[Entry] = [(rid, TraceRecord.model_validate(rec)) for rid, rec in rows]
        return ordered(entries), truncated

    async def trace(self, run_id: str) -> TraceTree:
        entries, truncated = await self._load(run_id)
        run, by_agent = totals(entries)
        return TraceTree(
            run_id=run_id,
            totals=run,
            by_agent=by_agent,
            truncated=truncated,
            tree=build_tree(entries),
        )

    async def flat(self, run_id: str) -> TraceFlat:
        entries, truncated = await self._load(run_id)
        run, by_agent = totals(entries)
        return TraceFlat(
            run_id=run_id,
            totals=run,
            by_agent=by_agent,
            truncated=truncated,
            records=[r.model_dump(mode="json") for _, r in entries],
        )

    async def runs(self, limit: int | None, after: str | None) -> RunList:
        n = self._default_limit if limit is None else limit
        if not 1 <= n <= self._max_limit:
            raise ValidationFailed(f"limit must be between 1 and {self._max_limit}")
        cursor = decode_cursor(after) if after else None
        found = await self._index.list_runs(n + 1, cursor)
        page = found[:n]
        more = len(found) > n
        nxt = encode_cursor(page[-1].started_at, page[-1].run_id) if more and page else None
        return RunList(runs=page, next=nxt)
