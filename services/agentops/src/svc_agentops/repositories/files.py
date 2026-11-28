"""Full trace records as JSONL, one file per run, on the PVC (docs/harness/12 §1).

The file is the source of truth; the Postgres index can always be rebuilt from it
(``python -m agentops.reindex``). Every line is ``{"id": <record_id>, "record": {...}}`` and readers
de-duplicate by id, so appending a record twice is harmless.

Appends take an exclusive ``flock``; one ``os.write`` carries the whole batch (``O_APPEND``) and is
fsynced. If the write fails (ENOSPC, EIO) the file is cut back to its size before the append, under
the same lock, so no torn line can glue itself to the next append; a file that nevertheless does not
end in a newline (a crash mid-write) is closed with one before the next batch. File names come only
from validated run ids and symlinks are never followed.
"""

import errno
import fcntl
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from common.logging import get_logger

_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_log = get_logger("trace_files")


class UnsafeRunId(ValueError):
    pass


class RunFull(Exception):
    """The run's file would grow past a per-run cap (``what`` is "bytes" or "records")."""

    def __init__(self, what: str, message: str) -> None:
        super().__init__(message)
        self.what = what


def _stored_ids(fd: int, size: int) -> set[str]:
    """Ids of the complete records already in the file (a torn line is not a stored record)."""
    ids: set[str] = set()
    if size == 0:
        return ids
    for line in os.pread(fd, size, 0).split(b"\n"):
        try:
            ids.add(str(json.loads(line)["id"]))
        except (ValueError, KeyError, TypeError):
            continue
    return ids


def _open(path: Path, flags: int, mode: int = 0o640) -> int:
    try:
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # the path is a symlink: never follow it
            raise UnsafeRunId("trace file is a symbolic link") from exc
        raise


class JsonlTraceFiles:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def prepare(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def writable(self) -> bool:
        return self._dir.is_dir() and os.access(self._dir, os.W_OK)

    def free_bytes(self) -> int:
        return shutil.disk_usage(self._dir).free

    def path_for(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise UnsafeRunId("run id must match [A-Za-z0-9_-]{1,64}")
        return self._dir / f"{run_id}.jsonl"  # the id has no separator, so it stays in the dir

    def run_ids(self) -> list[str]:
        return sorted(
            p.name.removesuffix(".jsonl")
            for p in self._dir.glob("*.jsonl")
            if _RUN_ID.fullmatch(p.name.removesuffix(".jsonl")) and not p.is_symlink()
        )

    def mtime(self, run_id: str) -> float | None:
        try:
            return self.path_for(run_id).lstat().st_mtime
        except FileNotFoundError:
            return None

    def append(
        self,
        run_id: str,
        items: list[tuple[str, dict[str, Any]]],
        max_bytes: int | None = None,
        max_records: int | None = None,
    ) -> int:
        """Append the records whose id is not in the file yet; returns how many were written.

        Under the exclusive lock the file is re-read for ids, so concurrent retries of one batch
        write it once and duplicates never count against the caps (bytes and records).
        """
        if not items:
            return 0
        path = self.path_for(run_id)
        fd = _open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            size = os.fstat(fd).st_size
            stored = _stored_ids(fd, size)
            fresh = [(rid, rec) for rid, rec in items if rid not in stored]
            if not fresh:
                return 0
            if max_records is not None and len(stored) + len(fresh) > max_records:
                raise RunFull("records", f"run {run_id} would exceed {max_records} records")
            data = "".join(
                json.dumps({"id": rid, "record": rec}, separators=(",", ":"), default=str) + "\n"
                for rid, rec in fresh
            ).encode()
            if size and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data  # a crash left a torn last line: end it, readers skip it
            if max_bytes is not None and size + len(data) > max_bytes:
                raise RunFull("bytes", f"run {run_id} would exceed {max_bytes} bytes")
            try:
                written = 0
                while written < len(data):
                    written += os.write(fd, data[written:])
                os.fsync(fd)
            except OSError:
                os.ftruncate(fd, size)  # undo a partial write before anyone else can append
                raise
            return len(fresh)
        finally:
            os.close(fd)  # closing releases the lock

    def delete_if_older(self, run_id: str, cutoff_ts: float) -> bool:
        """Delete the file under its lock, but only if it is still older than ``cutoff_ts``.

        An append in flight holds the lock, so purge waits for it and then sees the fresh mtime.
        """
        path = self.path_for(run_id)
        try:
            fd = _open(path, os.O_RDWR, 0o640)
        except FileNotFoundError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_mtime >= cutoff_ts:
                return False
            path.unlink(missing_ok=True)
            return True
        finally:
            os.close(fd)

    def exists(self, run_id: str) -> bool:
        return self.path_for(run_id).is_file()

    def read(self, run_id: str, limit: int) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
        """Records of one run, de-duplicated by id, at most ``limit``; flag says more existed."""
        path = self.path_for(run_id)
        seen: set[str] = set()
        out: list[tuple[str, dict[str, Any]]] = []
        truncated = False
        try:
            fd = _open(path, os.O_RDONLY)
        except FileNotFoundError:
            return [], False
        with os.fdopen(fd, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            for line in handle:
                try:
                    row = json.loads(line)
                    rid, rec = str(row["id"]), row["record"]
                    if not isinstance(rec, dict):
                        raise TypeError
                except (ValueError, KeyError, TypeError):
                    _log.warning("trace_line_skipped", run_id=run_id)  # a torn or foreign line
                    continue
                if rid in seen:
                    continue
                if len(out) >= limit:
                    truncated = True
                    break
                seen.add(rid)
                out.append((rid, rec))
        return out, truncated
