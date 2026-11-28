"""Postgres side of the trace store: ``ops.traces_index`` and ``ops.run_owners``."""

from dataclasses import asdict
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..models.traces import IndexRow, RunSummary

_INSERT = text("""
    INSERT INTO ops.traces_index
        (run_id, record_id, ts, kind, agent, service, step_id, parent_id, status,
         cost, tokens_in, tokens_out, latency)
    VALUES
        (:run_id, :record_id, :ts, :kind, :agent, :service, :step_id, :parent_id, :status,
         :cost, :tokens_in, :tokens_out, :latency)
    ON CONFLICT (run_id, record_id) DO NOTHING
    RETURNING record_id
""")

_RUNS = """
    SELECT t.run_id, min(t.ts) AS started_at, max(t.ts) AS ended_at, count(*) AS steps,
           sum(t.tokens_in) AS tokens_in, sum(t.tokens_out) AS tokens_out, sum(t.cost) AS cost,
           count(*) FILTER (WHERE t.status = 'error') AS errors
    FROM ops.traces_index t
    {join}
    GROUP BY t.run_id
    {having}
    ORDER BY min(t.ts) DESC, t.run_id DESC
    LIMIT :limit
"""


class PgTraceIndex:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def insert_new(self, rows: list[IndexRow]) -> set[str]:
        new: set[str] = set()
        async with self._engine.begin() as conn:
            for row in rows:  # <= 100 per request; RETURNING tells conflicts from inserts
                result = await conn.execute(_INSERT, asdict(row))
                if result.first() is not None:
                    new.add(row.record_id)
        return new

    async def existing(self, run_id: str, record_ids: list[str]) -> set[str]:
        if not record_ids:
            return set()
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT record_id FROM ops.traces_index "
                    "WHERE run_id = :r AND record_id = ANY(:ids)"
                ),
                {"r": run_id, "ids": record_ids},
            )
            return {r[0] for r in rows}

    async def count_run(self, run_id: str) -> int:
        async with self._engine.connect() as conn:
            row = await conn.execute(
                text("SELECT count(*) FROM ops.traces_index WHERE run_id = :r"), {"r": run_id}
            )
            return int(row.scalar() or 0)

    async def set_owner(self, run_id: str, user_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO ops.run_owners (run_id, user_id) VALUES (:r, :u) "
                    "ON CONFLICT (run_id) DO NOTHING"
                ),
                {"r": run_id, "u": user_id},
            )

    async def owner_of(self, run_id: str) -> str | None:
        async with self._engine.connect() as conn:
            row = await conn.execute(
                text("SELECT user_id FROM ops.run_owners WHERE run_id = :r"), {"r": run_id}
            )
            return row.scalar()

    async def list_runs(
        self, limit: int, after: tuple[datetime, str] | None, user_id: str | None = None
    ) -> list[RunSummary]:
        params: dict[str, object] = {"limit": limit}
        join = ""
        if user_id is not None:
            join = "JOIN ops.run_owners o ON o.run_id = t.run_id AND o.user_id = :uid"
            params["uid"] = user_id
        having = ""
        if after:
            having = "HAVING (min(t.ts), t.run_id) < (:a_ts, :a_run)"
            params |= {"a_ts": after[0], "a_run": after[1]}
        async with self._engine.connect() as conn:
            rows = (
                (await conn.execute(text(_RUNS.format(join=join, having=having)), params))
                .mappings()
                .all()
            )
        return [
            RunSummary(
                run_id=r["run_id"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                steps=r["steps"],
                tokens_in=r["tokens_in"],
                tokens_out=r["tokens_out"],
                cost_usd=float(r["cost"]),
                errors=r["errors"],
            )
            for r in rows
        ]

    async def old_runs(self, cutoff: datetime) -> list[str]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT run_id FROM ops.traces_index GROUP BY run_id "
                    "HAVING max(ts) < :c ORDER BY run_id"
                ),
                {"c": cutoff},
            )
            return [r[0] for r in rows]

    async def delete_runs(self, run_ids: list[str]) -> None:
        if not run_ids:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ops.traces_index WHERE run_id = ANY(:ids)"), {"ids": run_ids}
            )
            await conn.execute(
                text("DELETE FROM ops.run_owners WHERE run_id = ANY(:ids)"), {"ids": run_ids}
            )

    async def ping(self) -> bool:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
