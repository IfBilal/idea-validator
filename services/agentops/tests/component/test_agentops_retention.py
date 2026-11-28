import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from svc_agentops.repositories.files import JsonlTraceFiles
from svc_agentops.services.maintenance import purge
from svc_agentops.testing import MemoryIndex, Rig, rec

pytestmark = pytest.mark.asyncio


async def seed(rig: Rig, run_id: str, ts: datetime, owner: str | None = None) -> None:
    async with rig.client() as client:
        await client.post("/traces", json=[rec(1, run_id=run_id, ts=ts.isoformat())])
    if owner:
        rig.index.owners[run_id] = owner


def age_file(rig: Rig, run_id: str, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(rig.dir / f"{run_id}.jsonl", (old, old))


async def test_purge_removes_old_runs_from_files_and_index_but_keeps_recent_ones(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    now = datetime.now(UTC)
    await seed(rig, "run_old", now - timedelta(days=30), owner="u1")
    await seed(rig, "run_new", now - timedelta(days=1))
    age_file(rig, "run_old", 30)
    files = JsonlTraceFiles(rig.dir)
    doomed = await purge(files, rig.index, cutoff=now - timedelta(days=14))
    assert doomed == ["run_old"]
    assert files.run_ids() == ["run_new"]
    assert {k[0] for k in rig.index.rows} == {"run_new"}
    assert "run_old" not in rig.index.owners


async def test_dry_run_deletes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    now = datetime.now(UTC)
    await seed(rig, "run_old", now - timedelta(days=30))
    age_file(rig, "run_old", 30)
    doomed = await purge(
        JsonlTraceFiles(rig.dir), rig.index, cutoff=now - timedelta(days=14), dry_run=True
    )
    assert doomed == ["run_old"]
    assert (rig.dir / "run_old.jsonl").exists()
    assert rig.index.rows


async def test_a_run_with_old_records_but_a_freshly_written_file_is_kept(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    now = datetime.now(UTC)
    await seed(rig, "run_late", now - timedelta(days=30))  # late arrival: file mtime is now
    doomed = await purge(JsonlTraceFiles(rig.dir), rig.index, cutoff=now - timedelta(days=14))
    assert doomed == []
    assert (rig.dir / "run_late.jsonl").exists()


async def test_an_old_file_the_index_never_saw_is_removed(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    files = JsonlTraceFiles(rig.dir)
    files.append("run_lost", [("a", {})])
    age_file(rig, "run_lost", 60)
    doomed = await purge(files, MemoryIndex(), cutoff=datetime.now(UTC) - timedelta(days=14))
    assert doomed == ["run_lost"]
    assert files.run_ids() == []


async def test_purge_on_an_empty_store_is_a_no_op(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert await purge(JsonlTraceFiles(rig.dir), rig.index, cutoff=datetime.now(UTC)) == []
