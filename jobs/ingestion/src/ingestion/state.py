"""Resume cursor and the ``ingestion_runs`` log, as small files in the job's state directory.

The cursor says how many of the planned cards were stored. It is tied to a digest of the planned
cards: if the input changed since the interrupted run, the cursor is ignored and the run starts
from the beginning (storing a card twice is harmless, M6 is idempotent). A corrupt or unreadable
state file is treated as "no state", never as an error.
"""

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Cursor:
    digest: str
    done: int
    total: int


@dataclass
class RunRecord:
    """One row of ``ingestion_runs`` (docs/harness/04): counts per source plus the outcome."""

    run_id: str
    started_at: str
    ended_at: str
    status: str  # "ok" | "failed"
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    dropped: int = 0
    duplicates: int = 0
    resumed_from: int = 0
    added_by_source: dict[str, int] = field(default_factory=dict)
    error: str | None = None


class StateStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._cursor = directory / "cursor.json"
        self._runs = directory / "ingestion_runs.jsonl"

    def load(self) -> Cursor | None:
        try:
            data = json.loads(self._cursor.read_text())
            cursor = Cursor(str(data["digest"]), int(data["done"]), int(data["total"]))
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None
        if not 0 <= cursor.done <= cursor.total:
            return None
        return cursor

    def save(self, cursor: Cursor) -> None:
        self._write(self._cursor, json.dumps(asdict(cursor)))

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._cursor.unlink()

    def record(self, run: RunRecord) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._runs.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(run), sort_keys=True) + "\n")

    def runs(self) -> list[dict[str, Any]]:
        try:
            lines = self._runs.read_text().splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            with contextlib.suppress(ValueError, RecursionError):
                out.append(json.loads(line))
        return out

    def _write(self, path: Path, text: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=path.name)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(text)
            os.replace(tmp, path)  # atomic: a crash never leaves half a cursor
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
