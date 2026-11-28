"""Crash-safe files for the runner: atomic writes and a resumable progress log.

``progress.jsonl`` starts with a header line (schema, sha256 of the labelled file, image tag).
A resume is refused when the header does not match this run, so rows scored against another
labelled set or another build can never be mixed in. Rows are validated on load, and what the
human labelled (verdict, category, poisoned) always comes from the CURRENT labelled item, never
from the file.
"""

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.behaviour import CHECKS
from evaluation.labelled import LabelledItem
from evaluation.metrics import LABELS

Row = dict[str, Any]


class ProgressError(ValueError):
    """The progress file cannot be resumed from."""


def atomic_write(path: Path, text: str, *, exclusive: bool = False) -> None:
    """Write via a temp file in the same directory, then rename. ``exclusive`` refuses to replace
    an existing file (``FileExistsError``)."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(tmp, path)  # fails if the target exists
            os.unlink(tmp)
        else:
            os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_header(schema: str, labelled: Path, image_tag: str) -> dict[str, str]:
    return {"schema": schema, "labelled_sha256": file_digest(labelled), "image_tag": image_tag}


def start(path: Path, header: Mapping[str, str]) -> None:
    atomic_write(path, json.dumps({"header": dict(header)}, sort_keys=True) + "\n")


def append(path: Path, row: Mapping[str, Any]) -> None:
    line = json.dumps(row, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _valid_row(row: Any, ids: set[str]) -> bool:
    if not isinstance(row, dict) or row.get("status") != "ok" or row.get("id") not in ids:
        return False
    if row.get("system") not in LABELS or row.get("stability") not in ("STABLE", "BORDERLINE"):
        return False
    if not _finite(row.get("score")) or not _finite(row.get("confidence")):
        return False
    if not isinstance(row.get("run_id"), str):
        return False
    behaviour = row.get("behaviour")
    if not isinstance(behaviour, dict) or set(behaviour) != set(CHECKS):
        return False
    if any(v is not None and not isinstance(v, bool) for v in behaviour.values()):
        return False
    latency = row.get("agent_latency_ms", {})
    return isinstance(latency, dict) and all(_finite(v) for v in latency.values())


def load(
    path: Path, header: Mapping[str, str], items: Sequence[LabelledItem]
) -> tuple[dict[str, Row], int]:
    """Rows to keep (by id) and how many lines were dropped. Raises ``ProgressError`` on a
    missing file, a bad header or a header that is not this run's."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise ProgressError(f"{path}: no readable progress file to resume from") from None
    try:
        first = json.loads(lines[0]) if lines else None
    except ValueError:
        first = None
    if not isinstance(first, dict) or first.get("header") != dict(header):
        raise ProgressError(
            f"{path}: progress was written for another labelled set or build; "
            "run again without --resume"
        )
    by_id = {i.id: i for i in items}
    kept: dict[str, Row] = {}
    dropped = 0
    for line in lines[1:]:
        try:
            row = json.loads(line)
        except ValueError:
            dropped += 1  # a torn last line from a killed run: that item is simply run again
            continue
        if isinstance(row, dict) and row.get("status") == "error":
            continue  # an errored item is run again, nothing to report
        if not _valid_row(row, set(by_id)):
            dropped += 1
            continue
        item = by_id[row["id"]]
        row["human"], row["category"], row["poisoned"] = (
            item.human_verdict,
            item.category,
            item.poisoned,
        )
        kept[row["id"]] = row
    return kept, dropped
