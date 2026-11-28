"""``python -m agentops.reindex``: rebuild missing ``ops.traces_index`` rows from the files."""

import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from svc_agentops.config import Settings
from svc_agentops.repositories.files import JsonlTraceFiles
from svc_agentops.repositories.index import PgTraceIndex
from svc_agentops.services.maintenance import reindex


async def run(settings: Settings) -> int:
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        report = await reindex(JsonlTraceFiles(settings.trace_dir), PgTraceIndex(engine))
    finally:
        await engine.dispose()
    print(
        f"reindexed {report.runs} runs, {report.records} records, "
        f"{report.added} rows added, {report.skipped} skipped"
    )
    return 0


def main() -> int:
    return asyncio.run(run(Settings()))


if __name__ == "__main__":
    sys.exit(main())
