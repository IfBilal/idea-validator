"""Where raw items come from: a JSONL file today, the scouts' ``raw_pages`` table behind a Protocol.

Both yield ``RawItem`` or a ``Rejection`` for a row that cannot be read, so one malformed row is
counted and skipped instead of stopping the run.
"""

import asyncio
import gzip
import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ingestion.models import RawItem, Rejection

Row = RawItem | Rejection


class RawSource(Protocol):
    def read(self) -> Iterator[Row]: ...

    def commit(self) -> None:
        """Called once, after every card of the run is stored (mark raw rows processed)."""
        ...


def parse_row(index: int, data: object) -> Row:
    """One decoded JSON value to a ``RawItem``, or the reason it is unusable."""
    if not isinstance(data, dict):
        return Rejection(index=index, reason="row is not a JSON object")
    try:
        return RawItem.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return Rejection(
            index=index,
            reason=f"invalid row: {where}: {first['msg']}"[:200],
            url=str(data.get("url", ""))[:200],
        )


class JsonlSource:
    """``.jsonl`` or ``.jsonl.gz``: one raw item per line. Blank lines are ignored."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> Iterator[Row]:
        opener = gzip.open if self._path.suffix == ".gz" else open
        with opener(self._path, "rt", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    yield Rejection(index=index, reason="line is not valid JSON")
                    continue
                yield parse_row(index, data)

    def commit(self) -> None:
        return None


def row_to_item(index: int, row: Mapping[str, Any]) -> Row:
    """A ``raw_pages`` row (id, source, url, fetched_at, body) to a raw item.

    ``body`` is the scout's JSON object (or plain text); the row's own columns fill what it lacks.
    """
    body = row.get("body")
    if isinstance(body, str):
        try:
            decoded = json.loads(body)
        except ValueError:
            decoded = {"text": body}
        body = decoded if isinstance(decoded, dict) else {"text": body}
    if not isinstance(body, dict):
        body = {}
    merged: dict[str, Any] = {
        "source": row.get("source"),
        "url": row.get("url"),
        "fetched_at": row.get("fetched_at"),
        **body,
    }
    if isinstance(merged["fetched_at"], datetime):
        merged["fetched_at"] = merged["fetched_at"].isoformat()
    return parse_row(index, merged)


_QUERY = "SELECT id, source, url, fetched_at, body FROM raw_pages WHERE NOT processed ORDER BY id"
_MARK = "UPDATE raw_pages SET processed = true WHERE id = ANY($1::bigint[])"


class PostgresRawPages:
    """Unprocessed rows of the scouts' ``raw_pages`` table (docs/harness/04).

    The table is owned by M4; this reads it and, after a fully successful run, marks the rows it
    read as processed. Not exercised against a real database in unit tests: ``row_to_item`` is.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        self._ids: list[int] = []

    def read(self) -> Iterator[Row]:
        import asyncpg  # type: ignore[import-untyped]  # only this reader needs a database

        async def fetch() -> list[Mapping[str, Any]]:
            conn = await asyncpg.connect(self._dsn, timeout=10)
            try:
                return [dict(r) for r in await conn.fetch(_QUERY, timeout=60)]
            finally:
                await conn.close()

        rows = asyncio.run(fetch())
        self._ids = [int(r["id"]) for r in rows]
        for index, row in enumerate(rows):
            yield row_to_item(index, row)

    def commit(self) -> None:
        if not self._ids:
            return
        import asyncpg

        async def mark() -> None:
            conn = await asyncpg.connect(self._dsn, timeout=10)
            try:
                await conn.execute(_MARK, self._ids, timeout=60)
            finally:
                await conn.close()

        asyncio.run(mark())
