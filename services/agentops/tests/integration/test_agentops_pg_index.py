"""PgTraceIndex + the Alembic migration against a real Postgres (testcontainers)."""

import os
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svc_agentops.models.traces import IndexRow
from svc_agentops.repositories.index import PgTraceIndex
from svc_agentops.services.query import decode_cursor, encode_cursor
from svc_agentops.testing import T0

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def row(run: str, rid: str, secs: int = 0, **over: object) -> IndexRow:
    base: dict[str, object] = {
        "run_id": run,
        "record_id": rid,
        "ts": T0 + timedelta(seconds=secs),
        "kind": "tool_call",
        "agent": "a",
        "service": "orchestrator",
        "step_id": 1,
        "parent_id": None,
        "status": "ok",
        "cost": 0.5,
        "tokens_in": 3,
        "tokens_out": 4,
        "latency": 10,
    }
    return IndexRow(**(base | over))  # type: ignore[arg-type]


@pytest.fixture
def migrated_url(postgres_url: str) -> Iterator[str]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", "svc_agentops:migrations")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous
    yield postgres_url


@pytest.fixture
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE ops.traces_index"))
    yield engine
    await engine.dispose()


async def test_insert_is_idempotent_and_reports_only_new_ids(engine: AsyncEngine) -> None:
    index = PgTraceIndex(engine)
    assert await index.insert_new([row("r1", "a"), row("r1", "b")]) == {"a", "b"}
    assert await index.insert_new([row("r1", "b"), row("r1", "c")]) == {"c"}
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM ops.traces_index"))).scalar() == 3


async def test_delete_removes_only_the_named_records(engine: AsyncEngine) -> None:
    index = PgTraceIndex(engine)
    await index.insert_new([row("r1", "a"), row("r1", "b"), row("r2", "a")])
    await index.delete("r1", {"a"})
    async with engine.connect() as conn:
        left = (await conn.execute(text("SELECT run_id, record_id FROM ops.traces_index"))).all()
    assert sorted(tuple(r) for r in left) == [("r1", "b"), ("r2", "a")]
    await index.delete("r1", set())


async def test_runs_aggregate_and_page_by_started_at_then_run_id(engine: AsyncEngine) -> None:
    index = PgTraceIndex(engine)
    await index.insert_new(
        [
            row("r1", "a", 10),
            row("r1", "b", 20, status="error"),
            row("r2", "a", 100),
            row("r3", "a", 100),
        ]
    )
    first = await index.list_runs(2, None)
    assert [r.run_id for r in first] == ["r3", "r2"]
    rest = await index.list_runs(5, (first[-1].started_at, first[-1].run_id))
    assert [r.run_id for r in rest] == ["r1"]
    assert (rest[0].steps, rest[0].errors, rest[0].tokens_in, rest[0].cost_usd) == (2, 1, 6, 1.0)
    assert decode_cursor(encode_cursor(rest[0].started_at, "r1")) == (rest[0].started_at, "r1")
    assert await index.ping()


async def test_the_database_enforces_the_kind_and_status_values(engine: AsyncEngine) -> None:
    index = PgTraceIndex(engine)
    with pytest.raises(DBAPIError):
        await index.insert_new([row("r1", "a", kind="nonsense")])
    with pytest.raises(DBAPIError):
        await index.insert_new([row("r1", "b", status="weird")])


async def test_concurrent_inserts_of_the_same_record_yield_one_winner(engine: AsyncEngine) -> None:
    import asyncio

    index = PgTraceIndex(engine)
    results = await asyncio.gather(*[index.insert_new([row("r1", "same")]) for _ in range(10)])
    assert sum(len(r) for r in results) == 1
