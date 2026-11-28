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
        self.up = True

    async def insert_new(self, rows: list[IndexRow]) -> set[str]:
        new: set[str] = set()
        for r in rows:
            key = (r.run_id, r.record_id)
            if key not in self.rows:
                self.rows[key] = r
                new.add(r.record_id)
        return new

    async def delete(self, run_id: str, record_ids: set[str]) -> None:
        for rid in record_ids:
            self.rows.pop((run_id, rid), None)

    async def list_runs(self, limit: int, after: tuple[datetime, str] | None) -> list[RunSummary]:
        groups: dict[str, list[IndexRow]] = {}
        for r in self.rows.values():
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
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://t",
            headers=self.headers,
        )
