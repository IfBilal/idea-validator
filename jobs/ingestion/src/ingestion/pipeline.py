"""The ingestion run: read, map, dedupe, then post to M6 in capped batches with a resume cursor.

M6 mints the card id from the canonical URL, chunks the text (every chunk keeps its card id) and
indexes it, so this job sends cards, not chunks. A card seen on several platforms is posted once
for its representative source and once more per extra platform (identical content, so M6 records
the platform in ``card_sources`` and changes nothing else). Primaries go first, sightings after.

Re-running on the same input stores nothing new: ids come from the URL, M6 upserts, and the plan
is independent of input order. If M6 fails part-way the cursor keeps the number of cards already
stored and the next run continues from there (only if the planned cards are unchanged).
"""

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import structlog

from common.errors import DomainError, Forbidden, Unauthorized, ValidationFailed
from ingestion.config import MAX_BATCH
from ingestion.dedupe import DedupeConfig, dedupe
from ingestion.mappers import Rejected, map_item, url_label
from ingestion.models import CardDraft, KbClient, RawItem, Rejection
from ingestion.sources import RawSource
from ingestion.state import Cursor, RunRecord, StateStore

log = structlog.get_logger("ingestion")
MAX_KEPT_REJECTIONS = 200  # the report lists the first ones; the count is always exact


@dataclass
class RunReport:
    run_id: str
    status: str = "ok"
    read: int = 0
    rejected: int = 0
    rejections: list[Rejection] = field(default_factory=list)
    duplicates: int = 0
    planned: int = 0
    posted: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    dropped: int = 0
    resumed_from: int = 0
    id_mismatches: int = 0
    added_by_source: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "rejections"} | {
            "rejection_examples": [r.model_dump() for r in self.rejections[:5]]
        }


def _wire_items(plan_cards: list[Any]) -> list[dict[str, Any]]:
    """Primaries first, then one identical re-post per extra platform."""
    primaries = [p.card.wire() for p in plan_cards]
    sightings = [
        p.card.model_copy(update={"source": platform}).wire()
        for p in plan_cards
        for platform in p.platforms[1:]
    ]
    return primaries + sightings


def _digest(items: list[dict[str, Any]]) -> str:
    blob = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


async def run_ingestion(
    source: RawSource,
    kb: KbClient,
    *,
    state: StateStore,
    dedupe_config: DedupeConfig,
    batch_size: int = MAX_BATCH,
    today: date,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    resume: bool = True,
) -> RunReport:
    if not 1 <= batch_size <= MAX_BATCH:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH}, got {batch_size}")
    report = RunReport(run_id=uuid.uuid4().hex[:12])
    started = now()

    rows = await asyncio.to_thread(lambda: list(source.read()))  # a Postgres reader blocks
    drafts: list[CardDraft] = []
    for item in rows:
        report.read += 1
        if isinstance(item, Rejection):
            reject(report, item)
            continue
        assert isinstance(item, RawItem)
        try:
            drafts.append(map_item(item, fetched_on=today))
        except Rejected as exc:
            reject(report, Rejection(index=report.read - 1, reason=exc.reason, url=exc.url))
    plan = dedupe(drafts, dedupe_config)
    report.duplicates = plan.duplicates
    report.planned = len(plan.cards)
    wire = _wire_items(plan.cards)
    digest = _digest(wire)

    start = 0
    cursor = state.load() if resume else None
    if cursor is not None and cursor.digest == digest and cursor.total == len(wire):
        start = cursor.done
        report.resumed_from = start

    added_by_source: Counter[str] = Counter()
    pos = start
    try:
        while pos < len(wire):
            batch = wire[pos : pos + batch_size]
            for outcome, card in zip(await post_batch(kb, batch, report), batch, strict=False):
                if outcome is None:
                    continue
                if outcome.created:
                    report.added += 1
                    added_by_source[card["source"]] += 1
                elif outcome.changed:
                    report.updated += 1
                else:
                    report.unchanged += 1
            pos += len(batch)
            report.posted = pos - start
            state.save(Cursor(digest=digest, done=pos, total=len(wire)))
    except (Forbidden, Unauthorized):
        raise  # wrong credentials are a configuration error, not something a resume can fix
    except DomainError as exc:
        report.status = "failed"
        report.error = f"{type(exc).__name__}: {exc.message}"[:300]
        log.error("ingestion_failed", run_id=report.run_id, cursor=pos, error=report.error)
    report.added_by_source = dict(sorted(added_by_source.items()))

    if report.status == "ok":
        state.clear()
        await asyncio.to_thread(source.commit)
    state.record(
        RunRecord(
            run_id=report.run_id,
            started_at=started.isoformat(),
            ended_at=now().isoformat(),
            status=report.status,
            added=report.added,
            updated=report.updated,
            unchanged=report.unchanged,
            dropped=report.dropped + report.rejected,
            duplicates=report.duplicates,
            resumed_from=report.resumed_from,
            added_by_source=report.added_by_source,
            error=report.error,
        )
    )
    log.info("ingestion_run", **{k: v for k, v in report.summary().items() if k != "error"})
    return report


def reject(report: RunReport, rejection: Rejection) -> None:
    report.rejected += 1
    if len(report.rejections) < MAX_KEPT_REJECTIONS:
        report.rejections.append(rejection)


async def post_batch(kb: KbClient, batch: list[dict[str, Any]], report: RunReport) -> list[Any]:
    """Post one batch. A 422 for the batch is retried card by card so one bad card cannot stop
    the run; any other failure propagates (the cursor stays at the last stored batch)."""
    try:
        return list(await kb.ingest(batch))
    except ValidationFailed:
        if len(batch) == 1:
            _drop(report, batch[0])
            return [None]
    results: list[Any] = []
    for card in batch:
        try:
            results.extend(await kb.ingest([card]))
        except ValidationFailed:
            _drop(report, card)
            results.append(None)
    return results


def _drop(report: RunReport, card: dict[str, Any]) -> None:
    report.dropped += 1
    log.warning("card_rejected_by_kb", url=url_label(card["url"]))
