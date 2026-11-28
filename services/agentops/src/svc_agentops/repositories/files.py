"""Full trace records as JSONL, one file per run, on the PVC (docs/harness/12 §1).

Every line is ``{"id": <record_id>, "record": {...}}``. Appends take an exclusive ``flock`` so that
several writers (threads or processes) never interleave lines; one ``os.write`` carries the whole
batch (``O_APPEND``) and is fsynced. File names come only from validated run ids and are checked to
stay inside the directory; symlinks are never followed.
"""

import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any

from common.logging import get_logger

_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_log = get_logger("trace_files")


class UnsafeRunId(ValueError):
    pass


class JsonlTraceFiles:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def prepare(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def writable(self) -> bool:
        return self._dir.is_dir() and os.access(self._dir, os.W_OK)

    def path_for(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise UnsafeRunId("run id must match [A-Za-z0-9_-]{1,64}")
        path = (self._dir / f"{run_id}.jsonl").resolve()
        if path.parent != self._dir.resolve():
            raise UnsafeRunId("run id escapes the trace directory")
        return path

    def append(self, run_id: str, items: list[tuple[str, dict[str, Any]]]) -> None:
        if not items:
            return
        path = self.path_for(run_id)
        data = "".join(
            json.dumps({"id": rid, "record": rec}, separators=(",", ":"), default=str) + "\n"
            for rid, rec in items
        ).encode()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        finally:
            os.close(fd)  # closing releases the lock

    def exists(self, run_id: str) -> bool:
        return self.path_for(run_id).is_file()

    def read(self, run_id: str, limit: int) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
        """Records of one run, de-duplicated by id, at most ``limit``; flag says more existed."""
        path = self.path_for(run_id)
        seen: set[str] = set()
        out: list[tuple[str, dict[str, Any]]] = []
        truncated = False
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
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
