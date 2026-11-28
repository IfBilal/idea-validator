"""``python -m agentops.retention --days N [--dry-run]``: delete runs older than N days."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine

from svc_agentops.config import Settings
from svc_agentops.repositories.files import JsonlTraceFiles
from svc_agentops.repositories.index import PgTraceIndex
from svc_agentops.services.maintenance import purge


async def run(settings: Settings, days: int, dry_run: bool) -> int:
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        doomed = await purge(
            JsonlTraceFiles(settings.trace_dir),
            PgTraceIndex(engine),
            cutoff=cutoff,
            dry_run=dry_run,
        )
    finally:
        await engine.dispose()
    print(f"{'would delete' if dry_run else 'deleted'} {len(doomed)} runs older than {days} days")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentops.retention", description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2
    return asyncio.run(run(Settings(), args.days, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
