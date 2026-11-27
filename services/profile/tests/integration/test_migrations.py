import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from svc_profile.repositories.tables import SCHEMA, Base

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


async def _reset(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    await engine.dispose()


@pytest.fixture
def db(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    asyncio.run(_reset(postgres_url))
    yield postgres_url
    asyncio.run(_reset(postgres_url))


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": SCHEMA}
        )
        found = {r[0] for r in rows}
    await engine.dispose()
    return found


async def _drift(url: str) -> list[object]:
    engine = create_async_engine(url)

    def diff(connection) -> list[object]:  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            connection,
            opts={"include_schemas": True, "version_table_schema": SCHEMA, "compare_type": True},
        )
        return [d for d in compare_metadata(context, Base.metadata) if _in_app(d)]

    async with engine.connect() as conn:
        result = await conn.run_sync(diff)
    await engine.dispose()
    return result


def _in_app(diff: object) -> bool:
    return SCHEMA in repr(diff)


EXPECTED = {
    "users", "profiles", "ideas", "idea_versions", "verdicts", "dimension_scores",
    "approvals", "conversations", "runs", "alembic_version",
}  # fmt: skip


def test_upgrade_creates_every_application_table(db: str) -> None:
    command.upgrade(_config(), "head")
    assert asyncio.run(_tables(db)) == EXPECTED


def test_models_and_migration_agree(db: str) -> None:
    command.upgrade(_config(), "head")
    assert asyncio.run(_drift(db)) == []


def test_downgrade_removes_the_tables_and_upgrade_works_again(db: str) -> None:
    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")
    assert asyncio.run(_tables(db)) == {"alembic_version"}
    command.upgrade(_config(), "head")
    assert "users" in asyncio.run(_tables(db))


async def _insert_profile(url: str, **overrides: object) -> None:
    engine = create_async_engine(url)
    values = {
        "hours": 10, "weeks": 12, "team": 2, "compute": "laptop", "goal": "portfolio", "version": 1,
    }  # fmt: skip
    values.update(overrides)
    async with engine.begin() as conn:
        user_id = (
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.users (email, hash) VALUES ('a@b.co', 'h') RETURNING id"
                )
            )
        ).scalar_one()
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.profiles (user_id, version, skills, hours_per_week, "
                "weeks_available, team_size, compute, goal, interests) VALUES "
                "(:u, :version, '{}', :hours, :weeks, :team, :compute, :goal, '{}')"
            ),
            {"u": user_id, **values},
        )
    await engine.dispose()


@pytest.mark.parametrize(
    "bad",
    [{"hours": 81}, {"weeks": 0}, {"weeks": 53}, {"team": 11}, {"compute": "tpu"}, {"goal": "x"}],
)
def test_the_database_rejects_out_of_range_profiles(db: str, bad: dict[str, object]) -> None:
    command.upgrade(_config(), "head")
    with pytest.raises(DBAPIError):
        asyncio.run(_insert_profile(db, **bad))


def test_a_valid_profile_is_accepted(db: str) -> None:
    command.upgrade(_config(), "head")
    asyncio.run(_insert_profile(db))


async def _version_columns(url: str) -> dict[str, str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'idea_versions'"
            ),
            {"s": SCHEMA},
        )
        found = {r[0]: r[1] for r in rows}
    await engine.dispose()
    return found


async def _insert_versions(url: str, key_a: str | None, key_b: str | None) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            user_id = (
                await conn.execute(
                    text(
                        f"INSERT INTO {SCHEMA}.users (email, hash) VALUES ('a@b.co', 'h') "
                        "RETURNING id"
                    )
                )
            ).scalar_one()
            idea_id = (
                await conn.execute(
                    text(
                        f"INSERT INTO {SCHEMA}.ideas (user_id, title, source) "
                        "VALUES (:u, 't', 'own') RETURNING id"
                    ),
                    {"u": user_id},
                )
            ).scalar_one()
            for version, key in ((1, key_a), (2, key_b)):
                await conn.execute(
                    text(
                        f"INSERT INTO {SCHEMA}.idea_versions "
                        "(idea_id, version, text, idempotency_key) VALUES (:i, :v, 'x', :k)"
                    ),
                    {"i": idea_id, "v": version, "k": key},
                )
    finally:
        await engine.dispose()


def test_0002_adds_nullable_card_and_key_and_round_trips(db: str) -> None:
    command.upgrade(_config(), "0001")
    assert "card" not in asyncio.run(_version_columns(db))
    command.upgrade(_config(), "head")
    cols = asyncio.run(_version_columns(db))
    assert cols["card"] == cols["idempotency_key"] == "YES"
    command.downgrade(_config(), "0001")
    assert "card" not in asyncio.run(_version_columns(db))
    command.upgrade(_config(), "head")
    assert "card" in asyncio.run(_version_columns(db))


def test_idempotency_key_is_unique_per_idea_but_nulls_repeat(db: str) -> None:
    command.upgrade(_config(), "head")
    asyncio.run(_insert_versions(db, None, None))
    asyncio.run(_reset(db))
    command.upgrade(_config(), "head")
    with pytest.raises(DBAPIError):
        asyncio.run(_insert_versions(db, "same", "same"))
