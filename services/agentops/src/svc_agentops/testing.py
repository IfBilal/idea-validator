"""Test doubles and builders for M13: in-memory index (same paging semantics as Postgres), Rig."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from .config import Settings
from .main import build_app
from .models.traces import IndexRow, RunSummary


class MemoryIndex:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], IndexRow] = {}
        self.owners: dict[str, str] = {}
        self.up = True
        self.fail_inserts = False  # simulate a database error after the file was written

    async def insert_new(self, rows: list[IndexRow]) -> set[str]:
        if self.fail_inserts:
            raise ConnectionError("database down")
        new: set[str] = set()
        for r in rows:
            key = (r.run_id, r.record_id)
            if key not in self.rows:
                self.rows[key] = r
                new.add(r.record_id)
        return new

    async def existing(self, run_id: str, record_ids: list[str]) -> set[str]:
        return {rid for rid in record_ids if (run_id, rid) in self.rows}

    async def count_run(self, run_id: str) -> int:
        return sum(1 for r, _ in self.rows if r == run_id)

    async def set_owner(self, run_id: str, user_id: str) -> None:
        self.owners.setdefault(run_id, user_id)

    async def owner_of(self, run_id: str) -> str | None:
        return self.owners.get(run_id)

    async def list_runs(
        self, limit: int, after: tuple[datetime, str] | None, user_id: str | None = None
    ) -> list[RunSummary]:
        groups: dict[str, list[IndexRow]] = {}
        for r in self.rows.values():
            if user_id is None or self.owners.get(r.run_id) == user_id:
                groups.setdefault(r.run_id, []).append(r)
        summaries = []
        for run_id, rs in groups.items():
            started = min(r.ts for r in rs)
            if after is not None and (started, run_id) >= after:
                continue
            summaries.append(
                RunSummary(
                    run_id=run_id,
                    started_at=started,
                    ended_at=max(r.ts for r in rs),
                    steps=len(rs),
                    tokens_in=sum(r.tokens_in for r in rs),
                    tokens_out=sum(r.tokens_out for r in rs),
                    cost_usd=sum(r.cost for r in rs),
                    errors=sum(1 for r in rs if r.status == "error"),
                )
            )
        summaries.sort(key=lambda s: (s.started_at, s.run_id), reverse=True)
        return summaries[:limit]

    async def old_runs(self, cutoff: datetime) -> list[str]:
        newest: dict[str, datetime] = {}
        for r in self.rows.values():
            newest[r.run_id] = max(newest.get(r.run_id, r.ts), r.ts)
        return sorted(run for run, ts in newest.items() if ts < cutoff)

    async def delete_runs(self, run_ids: list[str]) -> None:
        for key in [k for k in self.rows if k[0] in run_ids]:
            del self.rows[key]
        for run in run_ids:
            self.owners.pop(run, None)

    async def ping(self) -> bool:
        return self.up


TOKEN = "t0ken"  # noqa: S105
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def rec(step: int = 1, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run_1",
        "step_id": step,
        "parent_step": None,
        "agent": "orchestrator",
        "kind": "tool_call",
        "tool": "t",
        "input": {"input_hash": "abc"},
        "output": {"ok": True},
        "tokens_in": 10,
        "tokens_out": 5,
        "cost_usd": 0.01,
        "latency_ms": 100,
        "retries": 0,
        "model": None,
        "cache_hit": False,
        "error": None,
        "ts": (T0 + timedelta(seconds=step)).isoformat(),
        "service": "orchestrator",
    }
    return base | over


class Rig:
    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        self.dir = tmp_path / "traces"
        self.index = MemoryIndex()
        self.settings = Settings(internal_token=SecretStr(TOKEN), trace_dir=self.dir, **settings)
        self.app = build_app(self.settings, index=self.index)
        self.headers = {"X-Internal-Token": TOKEN}
        self.dir.mkdir(parents=True, exist_ok=True)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://t",
            headers=self.headers,
        )
