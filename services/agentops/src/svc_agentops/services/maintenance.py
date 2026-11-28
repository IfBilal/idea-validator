"""Offline maintenance of the trace store: rebuild the index from the files, purge old runs."""

from dataclasses import dataclass
from datetime import datetime

from common.logging import get_logger
from common.schemas.trace import TraceRecord

from .ingest import OWNER_ID_MAX, index_row
from .ports import TraceFiles, TraceIndex
from .sanitize import privileged_name

log = get_logger("maintenance")
CHUNK = 100
READ_LIMIT = 1_000_000


@dataclass
class ReindexReport:
    runs: int = 0
    records: int = 0
    added: int = 0
    skipped: int = 0


async def reindex(files: TraceFiles, index: TraceIndex) -> ReindexReport:
    """Insert the index rows (and run owners) that the JSONL files have and Postgres lacks."""
    report = ReindexReport()
    for run_id in files.run_ids():
        rows, _ = files.read(run_id, READ_LIMIT)
        report.runs += 1
        parsed: list[tuple[str, TraceRecord]] = []
        for rid, raw in rows:
            try:
                parsed.append((rid, TraceRecord.model_validate(raw)))
            except ValueError:
                report.skipped += 1
                log.warning("reindex_skipped_record", run_id=run_id)
        report.records += len(parsed)
        for i in range(0, len(parsed), CHUNK):
            chunk = parsed[i : i + CHUNK]
            report.added += len(await index.insert_new([index_row(rid, r) for rid, r in chunk]))
        for _, record in parsed:
            if privileged_name(record) == "run_owner" and record.service == "orchestrator":
                user = record.output.get("user_id") if isinstance(record.output, dict) else None
                if isinstance(user, str) and 0 < len(user) <= OWNER_ID_MAX:
                    await index.set_owner(run_id, user)
    return report


async def purge(
    files: TraceFiles, index: TraceIndex, *, cutoff: datetime, dry_run: bool = False
) -> list[str]:
    """Delete runs older than ``cutoff``: both the file and the index rows. Returns the run ids."""
    old = set(await index.old_runs(cutoff))
    for run_id in files.run_ids():
        if (
            run_id not in old
            and files.mtime(run_id) is not None
            and await index.count_run(run_id) == 0
        ):
            old.add(run_id)  # a file the index never saw (or lost): age decided by the file
    doomed: list[str] = []
    for run_id in sorted(old):
        mtime = files.mtime(run_id)
        if mtime is None:
            doomed.append(run_id)  # no file: only index rows are left
        elif mtime < cutoff.timestamp():
            doomed.append(run_id)
    if dry_run:
        return doomed
    deleted: list[str] = []
    for run_id in doomed:
        # age is re-checked under the file lock: an append that landed meanwhile keeps the run
        if files.mtime(run_id) is None or files.delete_if_older(run_id, cutoff.timestamp()):
            deleted.append(run_id)
    await index.delete_runs(deleted)
    return deleted
