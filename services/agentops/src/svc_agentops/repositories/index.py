"""Postgres side of the trace store: ``ops.traces_index`` (one row per record)."""

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
    SELECT run_id, min(ts) AS started_at, max(ts) AS ended_at, count(*) AS steps,
           sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out, sum(cost) AS cost,
           count(*) FILTER (WHERE status = 'error') AS errors
    FROM ops.traces_index
    GROUP BY run_id
    {having}
    ORDER BY min(ts) DESC, run_id DESC
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

    async def delete(self, run_id: str, record_ids: set[str]) -> None:
        if not record_ids:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ops.traces_index WHERE run_id = :r AND record_id = ANY(:ids)"),
                {"r": run_id, "ids": sorted(record_ids)},
            )

    async def list_runs(self, limit: int, after: tuple[datetime, str] | None) -> list[RunSummary]:
        having = "HAVING (min(ts), run_id) < (:a_ts, :a_run)" if after else ""
        params: dict[str, object] = {"limit": limit}
        if after:
            params |= {"a_ts": after[0], "a_run": after[1]}
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(_RUNS.format(having=having)), params)).mappings().all()
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

    async def ping(self) -> bool:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
