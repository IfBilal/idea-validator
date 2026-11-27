"""``python -m ingestion run|snapshot|restore``.

Exit codes: 0 done, 1 the run failed part-way (re-run to resume), 2 bad usage or configuration,
3 the snapshot is missing, empty or untrustworthy. Every command prints one JSON line (the
report) as its last line of stdout, after the JSON log lines.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from common.errors import DomainError
from common.logging import configure_logging
from ingestion.client import HttpKbClient
from ingestion.config import Settings
from ingestion.models import KbClient
from ingestion.pipeline import RunReport, run_ingestion
from ingestion.snapshot import SnapshotError, latest_snapshot, restore_snapshot, write_snapshot
from ingestion.sources import JsonlSource, PostgresRawPages, RawSource
from ingestion.state import StateStore

DEFAULT_SNAPSHOT_DIR = Path("data/snapshots")
KbFactory = Callable[[Settings], KbClient]


class UsageError(Exception):
    pass


def make_kb(settings: Settings) -> KbClient:
    if not settings.kb_url:
        raise UsageError("KB_URL is not set")
    token = settings.internal_token.get_secret_value() if settings.internal_token else None
    return HttpKbClient(
        settings.kb_url,
        internal_token=token,
        timeout_s=settings.ingestion_timeout_s,
        max_retries=settings.ingestion_max_retries,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ingestion", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="raw items -> cards -> M6")
    run.add_argument("--input", type=Path, help="JSONL or JSONL.GZ of raw items")
    run.add_argument("--date", help="fetch date for items that carry none (default: today, UTC)")
    run.add_argument("--no-resume", action="store_true", help="ignore a saved cursor")
    snap = sub.add_parser("snapshot", help="write cards-YYYYMMDD.jsonl.gz from M6")
    snap.add_argument("--out-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    snap.add_argument("--date", help="snapshot date (default: today, UTC)")
    restore = sub.add_parser("restore", help="load a snapshot back through M6 /ingest")
    restore.add_argument("path", nargs="?", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    return parser


def _day(value: str | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise UsageError(f"not a date (YYYY-MM-DD): {value!r}") from None


def _source(args: argparse.Namespace, settings: Settings) -> RawSource:
    if args.input is not None:
        if not args.input.is_file():
            raise UsageError(f"input file not found: {args.input}")
        return JsonlSource(args.input)
    if settings.database_url is not None:
        return PostgresRawPages(settings.database_url.get_secret_value())
    raise UsageError("give --input FILE, or set DATABASE_URL to read the scouts' raw_pages")


def _emit(report: RunReport) -> None:
    print(json.dumps(report.summary(), sort_keys=True, default=str))


async def _run(args: argparse.Namespace, settings: Settings, kb: KbClient) -> int:
    source = _source(args, settings)
    report = await run_ingestion(
        source,
        kb,
        state=StateStore(settings.ingestion_state_dir),
        dedupe_config=settings.dedupe_config(),
        batch_size=settings.ingestion_batch_size,
        today=_day(args.date),
        resume=not args.no_resume,
    )
    _emit(report)
    return 0 if report.status == "ok" else 1


async def _snapshot(args: argparse.Namespace, kb: KbClient) -> int:
    manifest = await write_snapshot(kb, args.out_dir, day=_day(args.date))
    print(json.dumps(manifest.__dict__, sort_keys=True))
    return 0


async def _restore(args: argparse.Namespace, settings: Settings, kb: KbClient) -> int:
    report = await restore_snapshot(
        kb, latest_snapshot(args.path), batch_size=settings.ingestion_batch_size
    )
    _emit(report)
    return 0


def main(argv: list[str] | None = None, *, kb_factory: KbFactory = make_kb) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits on bad usage and on --help
        return int(exc.code) if isinstance(exc.code, int) else 2
    if args.command is None:
        _parser().print_usage(sys.stderr)
        return 2
    try:
        settings = Settings()
        configure_logging(settings.service_name, settings.log_level)
        kb = kb_factory(settings)
        runners: dict[str, Any] = {
            "run": lambda: _run(args, settings, kb),
            "snapshot": lambda: _snapshot(args, kb),
            "restore": lambda: _restore(args, settings, kb),
        }
        code: int = asyncio.run(runners[args.command]())
        return code
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    except SnapshotError as exc:
        print(f"snapshot error: {exc}", file=sys.stderr)
        return 3
    except DomainError as exc:
        print(f"knowledge base error: {exc.message}", file=sys.stderr)
        return 1
