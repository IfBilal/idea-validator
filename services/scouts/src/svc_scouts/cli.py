"""``python -m scouts --source <name> [--limit N]``: one run of one source, then exit.

Exit code 0: the run finished (ok, empty, partial, disabled or skipped by the circuit breaker),
1: the source failed (the CronJob retries per its backoffLimit), 2: bad arguments or config.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from common.config import ConfigError
from common.logging import configure_logging
from svc_scouts.models.sources import load_scout_sources

from .config import Settings
from .main import build_runner, tools_client
from .repositories.raw_pages import PgRawStore
from .services.connectors import CONNECTORS
from .services.errors import UnknownSource
from .services.metrics import ScoutMetrics
from .services.retention import prune_processed
from .services.runner import resolve_source


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scouts", description="Run one scout (M4) once.")
    parser.add_argument("--source", required=True, help=f"one of: {', '.join(sorted(CONNECTORS))}")
    parser.add_argument("--limit", type=int, default=None, help="max items stored this run")
    return parser


def _prune_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scouts prune", description="Delete processed raw_pages older than N days."
    )
    parser.add_argument("--older-than-days", type=int, required=True)
    return parser


async def _prune(argv: Sequence[str], settings: Settings) -> int:
    args = _prune_parser().parse_args(argv)
    if args.older_than_days < 1:
        print("--older-than-days must be >= 1", file=sys.stderr)
        return 2
    if not settings.database_url:
        print("DATABASE_URL must be set", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        store = PgRawStore(async_sessionmaker(engine, expire_on_commit=False))
        deleted = await prune_processed(
            store, older_than_days=args.older_than_days, now=datetime.now(UTC)
        )
    finally:
        await engine.dispose()
    print(json.dumps({"pruned": deleted}))
    return 0


async def run_cli(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["prune"]:
        return await _prune(args_list[1:], settings or Settings())
    args = _parser().parse_args(args_list)
    if args.limit is not None and args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr)
        return 2
    try:
        source = resolve_source(args.source)
    except UnknownSource as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = settings or Settings()
    configure_logging("scouts", settings.log_level)
    tools = tools_client(settings)
    if not settings.database_url or tools is None:
        print("DATABASE_URL and TOOLS_URL must be set", file=sys.stderr)
        return 2
    try:
        sources = load_scout_sources(settings.config_dir)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        runner = build_runner(
            settings,
            store=PgRawStore(async_sessionmaker(engine, expire_on_commit=False)),
            fetcher=tools,
            sources=sources,
            metrics=ScoutMetrics(),
        )
        report = await runner.run(source, limit=args.limit)
    finally:
        await tools.aclose()
        await engine.dispose()
    print(report.model_dump_json())
    return 1 if report.status == "failed" else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_cli(argv))
