"""LangGraph checkpointer on Postgres, schema ``orchestrator`` (docs/harness/29 §2).

Checkpoints are what make a run survive a pod restart: ``thread_id = run_id``, so the run resumes
exactly where ``interrupt()`` stopped it. Checkpoints are read back through the strict msgpack
allow-list of ``serde.py``. Unit tests use ``MemorySaver`` instead.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from .serde import checkpoint_serde

SCHEMA = "orchestrator"
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_DRIVER_RE = re.compile(r"^(postgres(?:ql)?)(\+\w+)?://")


def psycopg_url(database_url: str) -> str:
    """Turn a SQLAlchemy-style URL (``postgresql+asyncpg://``) into a plain libpq one."""
    match = _DRIVER_RE.match(database_url)
    if match is None:
        raise ValueError("DATABASE_URL must be a postgresql:// URL")
    return "postgresql://" + database_url[match.end() :]


def conninfo_for(database_url: str, schema: str = SCHEMA) -> str:
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"invalid schema name {schema!r}")
    return make_conninfo(psycopg_url(database_url), options=f"-c search_path={schema}")


@asynccontextmanager
async def postgres_saver(
    database_url: str, schema: str = SCHEMA
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open the saver, creating the schema and tables on first use."""
    info = conninfo_for(database_url, schema)  # validates the schema name before any SQL
    async with await psycopg.AsyncConnection.connect(
        psycopg_url(database_url), autocommit=True
    ) as admin:
        await admin.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    async with AsyncPostgresSaver.from_conn_string(info, serde=checkpoint_serde()) as saver:
        await saver.setup()
        yield saver
