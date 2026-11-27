"""Snapshot and restore of the card corpus (docs/harness/10 section 1 and 5).

``snapshot`` pages through M6 ``GET /cards`` and writes ``cards-YYYYMMDD.jsonl.gz`` (sorted by id,
sorted keys, gzip with a zero timestamp: the same corpus gives the same bytes) plus a manifest
with counts per source and the sha256 of the archive. Only public card fields are written; the
volatile ``first_seen_at`` / ``last_seen_at`` are left out.

``restore`` reads a snapshot back through M6 ``POST /ingest``. It is idempotent (M6 upserts by
URL), verifies the manifest checksum when there is one, drops rows that are malformed or whose
text no longer matches its content hash, and records every platform of every card.
"""

import asyncio
import contextlib
import gzip
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from ingestion.config import MAX_BATCH
from ingestion.mappers import content_hash_of
from ingestion.models import CardDraft, KbClient, Rejection
from ingestion.pipeline import RunReport, post_batch, reject

log = structlog.get_logger("ingestion")

_FIELDS = (
    "id",
    "name",
    "summary",
    "problem",
    "customer",
    "niche_tags",
    "stage",
    "funding_usd",
    "source",
    "kind",
    "url",
    "published_at",
    "fetched_at",
    "text",
    "content_hash",
    "platforms",
)
_NAME = re.compile(r"^cards-(\d{8})\.jsonl\.gz$")
_MAX_PAGES = 100_000


class SnapshotError(Exception):
    """The snapshot cannot be written or trusted; the command exits non-zero."""


@dataclass(frozen=True)
class Manifest:
    file: str
    date: str
    total: int
    by_source: dict[str, int]
    by_platform: dict[str, int]
    by_kind: dict[str, int]
    sha256: str
    format: int = 1


def _manifest_path(path: Path) -> Path:
    return path.with_name(path.name.removesuffix(".jsonl.gz") + ".manifest.json")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


async def write_snapshot(
    kb: KbClient, out_dir: Path, *, day: date, page_size: int = 500
) -> Manifest:
    rows: dict[str, dict[str, Any]] = {}
    offset = total = 0
    for _ in range(_MAX_PAGES):
        items, total = await kb.list_cards(limit=page_size, offset=offset)
        for item in items:
            rows[item["id"]] = {k: item.get(k) for k in _FIELDS}
        offset += len(items)
        if not items or offset >= total:
            break
    if not rows:
        raise SnapshotError("the knowledge base is empty: refusing to write an empty snapshot")
    if len(rows) != total:
        raise SnapshotError(
            f"read {len(rows)} cards but M6 reports {total}: the corpus changed while reading"
        )
    ordered = [rows[i] for i in sorted(rows)]
    for row in ordered:
        row["platforms"] = sorted(row["platforms"] or [row["source"]])
    payload = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in ordered)
    blob = gzip.compress(payload.encode("utf-8"), mtime=0)
    stem = f"cards-{day:%Y%m%d}"
    path = out_dir / f"{stem}.jsonl.gz"
    manifest = Manifest(
        file=path.name,
        date=day.isoformat(),
        total=len(ordered),
        by_source=dict(sorted(Counter(r["source"] for r in ordered).items())),
        by_platform=dict(sorted(Counter(p for r in ordered for p in r["platforms"]).items())),
        by_kind=dict(sorted(Counter(r["kind"] for r in ordered).items())),
        sha256=hashlib.sha256(blob).hexdigest(),
    )
    _atomic_write(path, blob)
    _atomic_write(
        _manifest_path(path),
        (json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n").encode(),
    )
    log.info("snapshot_written", file=path.name, total=manifest.total, sha256=manifest.sha256)
    return manifest


def latest_snapshot(location: Path) -> Path:
    """``location`` itself if it is a file, else the newest ``cards-YYYYMMDD.jsonl.gz`` in it."""
    if location.is_file():
        return location
    if location.is_dir():
        found = sorted(p for p in location.iterdir() if _NAME.match(p.name))
        if found:
            return found[-1]
    raise SnapshotError(f"no snapshot found at {location}")


def _read_rows(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SnapshotError(f"cannot read {path}: {exc.strerror}") from exc
    manifest = _manifest_path(path)
    if manifest.exists():
        try:
            expected = json.loads(manifest.read_text())["sha256"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SnapshotError(f"unreadable manifest {manifest.name}") from exc
        if hashlib.sha256(raw).hexdigest() != expected:
            raise SnapshotError(f"checksum mismatch for {path.name}: the file is not the snapshot")
    try:
        return gzip.decompress(raw).decode("utf-8", errors="replace").splitlines()
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise SnapshotError(f"{path.name} is not a readable gzip archive") from exc


def _row_to_wire(index: int, line: str) -> tuple[CardDraft, str, list[str]] | Rejection:
    try:
        data = json.loads(line)
    except ValueError:
        return Rejection(index=index, reason="line is not valid JSON")
    if not isinstance(data, dict):
        return Rejection(index=index, reason="row is not a JSON object")
    card_id = str(data.pop("id", ""))
    platforms = [p for p in data.pop("platforms", []) if isinstance(p, str)]
    try:
        card = CardDraft.model_validate(data)
    except ValidationError as exc:
        return Rejection(index=index, reason=f"invalid card: {exc.errors()[0]['msg']}"[:200])
    if content_hash_of(card.text) != card.content_hash:
        return Rejection(index=index, reason="text does not match its content hash", url=card.url)
    return card, card_id, platforms


async def restore_snapshot(kb: KbClient, path: Path, *, batch_size: int = MAX_BATCH) -> RunReport:
    if not 1 <= batch_size <= MAX_BATCH:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH}, got {batch_size}")
    lines = await asyncio.to_thread(_read_rows, path)
    report = RunReport(run_id="restore")
    cards: list[CardDraft] = []
    ids: list[str] = []
    sightings: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        report.read += 1
        parsed = _row_to_wire(index, line)
        if isinstance(parsed, Rejection):
            reject(report, parsed)
            report.dropped += 1
            continue
        card, card_id, platforms = parsed
        cards.append(card)
        ids.append(card_id)
        sightings += [
            card.model_copy(update={"source": p}).wire()
            for p in sorted(platforms)
            if p != card.source
        ]
    if not cards:
        raise SnapshotError(f"no cards in {path.name}")
    report.rejected = 0  # rows are counted once, as dropped
    wire = [c.wire() for c in cards] + sightings
    for pos in range(0, len(wire), batch_size):
        batch = wire[pos : pos + batch_size]
        results = await post_batch(kb, batch, report)
        for offset, outcome in enumerate(results):
            if outcome is None:
                continue
            if outcome.created:
                report.added += 1
            elif outcome.changed:
                report.updated += 1
            else:
                report.unchanged += 1
            index = pos + offset
            if index < len(ids) and ids[index] and outcome.card_id != ids[index]:
                report.id_mismatches += 1
                log.warning("restored_card_id_differs", expected=ids[index], got=outcome.card_id)
    report.planned = len(wire)
    report.posted = len(wire)
    return report
