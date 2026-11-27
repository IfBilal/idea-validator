"""PgRawStore + the Alembic migration against a real Postgres (testcontainers)."""

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from svc_scouts.models.items import RawItem
from svc_scouts.models.runs import ScoutState
from svc_scouts.repositories.raw_pages import PgRawStore

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def item(url: str = "https://example.com/a", title: str = "t", text_: str = "") -> RawItem:
    return RawItem(
        source="hackernews", url=url, title=title, text=text_, platform="hackernews", score=3
    )


@pytest.fixture
def migrated_url(postgres_url: str) -> Iterator[str]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", "svc_scouts:migrations")
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
        await conn.execute(text("TRUNCATE scouts.raw_pages, scouts.scout_state"))
    yield engine
    await engine.dispose()


@pytest.fixture
def store(engine: AsyncEngine) -> PgRawStore:
    return PgRawStore(async_sessionmaker(engine, expire_on_commit=False))


async def test_upsert_is_idempotent_and_counts_added_updated_unchanged(store: PgRawStore) -> None:
    assert await store.upsert_items([item("https://e.com/1"), item("https://e.com/2")]) == (2, 0, 0)
    assert await store.upsert_items([item("https://e.com/1"), item("https://e.com/2")]) == (0, 0, 2)
    assert await store.upsert_items([item("https://e.com/1", title="new")]) == (0, 1, 0)


async def test_changed_text_resets_processed_unchanged_keeps_it(
    store: PgRawStore, engine: AsyncEngine
) -> None:
    await store.upsert_items([item("https://e.com/1"), item("https://e.com/2")])
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE scouts.raw_pages SET processed = true"))
    await store.upsert_items([item("https://e.com/1", title="changed"), item("https://e.com/2")])
    async with engine.connect() as conn:
        found = (await conn.execute(text("SELECT url, processed FROM scouts.raw_pages"))).all()
        rows = {r[0]: r[1] for r in found}
    assert rows == {"https://e.com/1": False, "https://e.com/2": True}


async def test_the_body_is_the_stable_raw_item_json(store: PgRawStore, engine: AsyncEngine) -> None:
    await store.upsert_items([item(text_="hello")])
    async with engine.connect() as conn:
        body = (await conn.execute(text("SELECT body FROM scouts.raw_pages"))).scalar_one()
    assert body["title"] == "t" and body["text"] == "hello" and body["platform"] == "hackernews"
    assert set(body) == {
        "source",
        "url",
        "title",
        "text",
        "created_at",
        "score",
        "platform",
        "metadata",
    }


async def test_a_large_batch_and_an_empty_batch(store: PgRawStore) -> None:
    batch = [item(f"https://e.com/{i}") for i in range(450)]  # > the 200 lookup chunk
    assert await store.upsert_items(batch) == (450, 0, 0)
    assert await store.upsert_items(batch) == (0, 0, 450)
    assert await store.upsert_items([]) == (0, 0, 0)


async def test_the_unique_key_and_source_check_are_enforced(engine: AsyncEngine) -> None:
    insert = (
        "INSERT INTO scouts.raw_pages (source, url, body, content_hash) VALUES (:s, :u, '{}', 'h')"
    )
    async with engine.begin() as conn:
        await conn.execute(text(insert), {"s": "hackernews", "u": "https://e.com/x"})
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text(insert), {"s": "hackernews", "u": "https://e.com/x"})
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text(insert), {"s": "myspace", "u": "https://e.com/y"})


async def test_state_round_trip_including_the_breaker_fields(store: PgRawStore) -> None:
    assert await store.get_state("github") == ScoutState()
    opened = datetime(2026, 11, 27, 12, tzinfo=UTC)
    state = ScoutState(
        cursor={"pushed_since": "2026-11-27", "n": [1, 2]},
        failures=3,
        opened_at=opened,
        last_run_at=opened,
        last_status="failed",
        last_error="FetchFailed: x",
    )
    await store.save_state("github", state)
    assert await store.get_state("github") == state
    await store.save_state("github", ScoutState(cursor={"a": 1}))
    assert (await store.get_state("github")).cursor == {"a": 1}


async def test_the_advisory_lock_is_exclusive_per_source_and_released(
    store: PgRawStore, engine: AsyncEngine
) -> None:
    other = PgRawStore(async_sessionmaker(engine, expire_on_commit=False))
    async with store.lock("github") as first:
        assert first is True
        async with other.lock("github") as second:
            assert second is False  # a second process would be refused
        async with other.lock("lobsters") as different:
            assert different is True
    async with other.lock("github") as again:
        assert again is True  # released in finally


async def test_prune_deletes_processed_old_rows_only(
    store: PgRawStore, engine: AsyncEngine
) -> None:
    await store.upsert_items([item("https://e.com/1"), item("https://e.com/2")])
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE scouts.raw_pages SET processed = true, fetched_at = now() - interval '40 days' "
                "WHERE url = 'https://e.com/1'"
            )
        )
        await conn.execute(
            text(
                "UPDATE scouts.raw_pages SET fetched_at = now() - interval '40 days' WHERE url = 'https://e.com/2'"
            )
        )
    cutoff = datetime.now(UTC) - timedelta(days=30)
    assert await store.prune(cutoff) == 1
    async with engine.connect() as conn:
        left = (await conn.execute(text("SELECT url FROM scouts.raw_pages"))).scalars().all()
    assert left == ["https://e.com/2"]  # unprocessed rows are never pruned


async def test_ping(store: PgRawStore) -> None:
    assert await store.ping() is True
