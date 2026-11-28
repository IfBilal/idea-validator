"""Ingest ordering (file first, then index), failure injection, caps, ownership, forged decisions."""

import asyncio
import errno
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from svc_agentops.repositories.files import JsonlTraceFiles
from svc_agentops.services.maintenance import reindex
from svc_agentops.testing import T0, Rig, rec

pytestmark = pytest.mark.asyncio


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def at(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def owner(run_id: str, user: str, service: str = "orchestrator") -> dict[str, Any]:
    return rec(
        1,
        run_id=run_id,
        kind="decision",
        service=service,
        tool="transition",
        output={"decision": "run_owner", "user_id": user},
    )


# ---- file first, then index ----------------------------------------------------------------
async def test_a_failing_append_stores_nothing_and_a_retry_succeeds(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = JsonlTraceFiles.append
    calls = {"n": 0}

    def flaky(self: JsonlTraceFiles, *args: Any, **kw: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EIO, "disk error")
        real(self, *args, **kw)

    monkeypatch.setattr(JsonlTraceFiles, "append", flaky)
    async with rig.client() as client:
        first = await client.post("/traces", json=[rec(1)])
        assert first.status_code == 507
        assert rig.index.rows == {}
        retry = await client.post("/traces", json=[rec(1)])
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    assert retry.json()["accepted"] == 1
    assert len(body["records"]) == 1


async def test_a_concurrent_retry_during_a_failing_append_is_never_told_duplicate_then_lost(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = JsonlTraceFiles.append
    gate = {"first": True}

    def slow_then_fail(self: JsonlTraceFiles, *args: Any, **kw: Any) -> None:
        if gate["first"]:
            gate["first"] = False
            import time

            time.sleep(0.2)  # the retry arrives while this append is in flight
            raise OSError(errno.ENOSPC, "no space")
        real(self, *args, **kw)

    monkeypatch.setattr(JsonlTraceFiles, "append", slow_then_fail)
    async with rig.client() as client:
        a, b = await asyncio.gather(
            client.post("/traces", json=[rec(1)]), client.post("/traces", json=[rec(1)])
        )
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    results = sorted([a.status_code, b.status_code])
    assert results == [200, 507]
    ok = a if a.status_code == 200 else b
    assert ok.json()["accepted"] == 1  # the success is real: the record is stored
    assert ok.json()["duplicates"] == 0
    assert len(body["records"]) == 1


async def test_a_crash_after_the_file_write_leaves_a_line_that_reindex_restores(
    rig: Rig,
) -> None:
    rig.index.fail_inserts = True
    async with rig.client() as client:
        response = await client.post("/traces", json=[rec(1), rec(2)])
    assert response.status_code == 500
    assert rig.index.rows == {}
    files = JsonlTraceFiles(rig.dir)
    assert len(files.read("run_1", 100)[0]) == 2  # nothing was lost

    rig.index.fail_inserts = False
    report = await reindex(files, rig.index)
    assert (report.runs, report.records, report.added) == (1, 2, 2)
    assert len(rig.index.rows) == 2
    again = await reindex(files, rig.index)
    assert again.added == 0  # idempotent


async def test_a_retry_after_an_index_failure_does_not_duplicate_the_record(rig: Rig) -> None:
    rig.index.fail_inserts = True
    async with rig.client() as client:
        await client.post("/traces", json=[rec(1)])
        rig.index.fail_inserts = False
        retry = await client.post("/traces", json=[rec(1)])
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    assert retry.json()["accepted"] == 1
    assert len(body["records"]) == 1  # the doubled line is de-duplicated on read
    assert len(rig.index.rows) == 1


async def test_reindex_restores_run_owners_from_the_files(rig: Rig) -> None:
    rig.index.fail_inserts = True
    async with rig.client() as client:
        await client.post("/traces", json=[owner("run_1", "u1")])
    rig.index.fail_inserts = False
    await reindex(JsonlTraceFiles(rig.dir), rig.index)
    assert rig.index.owners == {"run_1": "u1"}


# ---- growth ---------------------------------------------------------------------------------
async def test_a_run_is_capped_in_bytes_and_in_records(tmp_path: Path) -> None:
    small = Rig(tmp_path / "a", max_run_bytes=1500, max_run_records=1000)
    async with small.client() as client:
        first = await client.post("/traces", json=[rec(1)])
        second = await client.post("/traces", json=[rec(i) for i in range(2, 12)])
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert "limit" in second.json()["rejected"][0]["reason"]
    assert (small.dir / "run_1.jsonl").stat().st_size <= 1500

    few = Rig(tmp_path / "b", max_run_records=3)
    async with few.client() as client:
        await client.post("/traces", json=[rec(1), rec(2), rec(3)])
        over = await client.post("/traces", json=[rec(4)])
        other = await client.post("/traces", json=[rec(1, run_id="run_2")])
    assert over.json()["rejected"][0]["index"] == 0
    assert other.json()["accepted"] == 1  # another run is unaffected


async def test_low_disk_space_refuses_ingest_with_507_and_fails_ready(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(JsonlTraceFiles, "free_bytes", lambda self: 1024)
    async with rig.client() as client:
        response = await client.post("/traces", json=[rec(1)])
        ready = await client.get("/ready")
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "INTERNAL"
    assert ready.status_code == 503
    assert ready.json()["checks"]["trace_dir"] is False


# ---- ownership and forged decisions --------------------------------------------------------
async def test_the_run_owner_scopes_the_trace_and_the_run_list(rig: Rig) -> None:
    async with rig.client() as client:
        await client.post("/traces", json=[owner("run_a", "u1"), rec(2, run_id="run_a")])
        await client.post("/traces", json=[owner("run_b", "u2"), rec(2, run_id="run_b")])
        await client.post("/traces", json=[rec(1, run_id="run_orphan")])
        mine = (await client.get("/runs", params={"user_id": "u1"})).json()["runs"]
        everyone = (await client.get("/runs")).json()["runs"]
        ok = await client.get("/runs/run_a/trace", params={"user_id": "u1"})
        other = await client.get("/runs/run_a/trace", params={"user_id": "u2"})
        unowned = await client.get("/runs/run_orphan/trace", params={"user_id": "u1"})
        admin = await client.get("/runs/run_a/trace")
        bad = await client.get("/runs", params={"user_id": "a b;c"})
    assert [r["run_id"] for r in mine] == ["run_a"]
    assert len(everyone) == 3
    assert ok.status_code == 200
    assert other.status_code == 404  # the same answer as an unknown run
    assert unowned.status_code == 404
    assert admin.status_code == 200
    assert bad.status_code == 422


async def test_the_first_owner_wins(rig: Rig) -> None:
    async with rig.client() as client:
        await client.post("/traces", json=[owner("run_a", "u1")])
        await client.post("/traces", json=[owner("run_a", "u2")])
    assert rig.index.owners["run_a"] == "u1"


@pytest.mark.parametrize("decision", ["m11_verdict", "run_owner"])
async def test_privileged_decisions_from_other_services_are_rejected(
    rig: Rig, decision: str
) -> None:
    forged = rec(
        2,
        kind="decision",
        service="tools",
        agent="tools",
        output={"decision": decision, "user_id": "victim", "verdict": "GO"},
    )
    async with rig.client() as client:
        response = await client.post("/traces", json=[forged])
    body = response.json()
    assert body["accepted"] == 0
    assert "orchestrator" in body["rejected"][0]["reason"]
    assert rig.index.owners == {}


async def test_a_realistic_m11_input_over_4kb_is_stored_whole(rig: Rig) -> None:
    scores = [
        {
            "dimension": d,
            "score": 3.5,
            "confidence": 0.8,
            "evidence_ids": [f"card_{i:08x}#c{j}" for j in range(40)],
        }
        for i, d in enumerate(["a", "b", "c", "d", "e", "f"])
    ]
    record = rec(
        2,
        kind="decision",
        tool="transition",
        input={"scores": scores, "profile": {"goal": "startup"}},
        output={"decision": "m11_verdict", "verdict": "GO"},
    )
    async with rig.client() as client:
        response = await client.post("/traces", json=[record])
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    assert response.json()["accepted"] == 1
    stored = body["records"][0]["input"]
    assert "_truncated" not in stored
    assert stored["scores"][0]["evidence_ids"] == scores[0]["evidence_ids"]


# ---- symlink and unsafe names at the HTTP edge -----------------------------------------------
async def test_a_symlinked_trace_file_is_never_followed_by_reads_or_writes(
    rig: Rig, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text("secret\n")
    os.symlink(outside, rig.dir / "run_evil.jsonl")
    async with rig.client() as client:
        read = await client.get("/runs/run_evil/trace")
        write = await client.post("/traces", json=[rec(1, run_id="run_evil")])
    assert read.status_code == 404
    assert write.json()["accepted"] == 0
    assert outside.read_text() == "secret\n"


async def test_a_full_disk_error_mid_write_leaves_the_file_unchanged(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.client() as client:
        await client.post("/traces", json=[rec(1)])
        path = rig.dir / "run_1.jsonl"
        before = path.read_bytes()
        real_write = os.write

        def half_write(fd: int, data: bytes) -> int:
            real_write(fd, data[: len(data) // 2])  # a torn write...
            raise OSError(errno.ENOSPC, "no space left")

        monkeypatch.setattr(os, "write", half_write)
        failed = await client.post("/traces", json=[rec(2)])
        monkeypatch.undo()
        assert failed.status_code == 507
        assert path.read_bytes() == before  # ...is cut back under the lock
        ok = await client.post("/traces", json=[rec(3)])
    assert ok.json()["accepted"] == 1
    assert [line for line in path.read_text().splitlines() if line.startswith("{")]


async def test_a_user_with_a_uuid_or_date_like_id_can_read_their_own_trace(rig: Rig) -> None:
    for user in ("12345678-1234-4abc-8def-0123456789ab", "20261128-123456"):
        run = f"run_{user[:8]}"
        async with rig.client() as client:
            await client.post("/traces", json=[owner(run, user), rec(2, run_id=run)])
            mine = await client.get(f"/runs/{run}/trace", params={"user_id": user})
            listed = await client.get("/runs", params={"user_id": user})
        assert mine.status_code == 200
        assert [r["run_id"] for r in listed.json()["runs"]] == [run]


async def test_a_retry_repairs_a_missing_owner_even_though_the_record_is_a_duplicate(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = rig.index.set_owner
    calls = {"n": 0}

    async def flaky(run_id: str, user_id: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database blip")
        await real(run_id, user_id)

    monkeypatch.setattr(rig.index, "set_owner", flaky)
    async with rig.client() as client:
        first = await client.post("/traces", json=[owner("run_a", "u1")])
        assert first.status_code == 500
        assert rig.index.owners == {}
        retry = await client.post("/traces", json=[owner("run_a", "u1")])
        mine = await client.get("/runs/run_a/trace", params={"user_id": "u1"})
    assert retry.json()["duplicates"] == 1
    assert rig.index.owners == {"run_a": "u1"}
    assert mine.status_code == 200


async def test_a_timestamp_more_than_a_day_ahead_is_rejected_so_retention_cannot_be_bypassed(
    rig: Rig,
) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    future = (now + timedelta(days=3650)).isoformat()
    soon = (now + timedelta(hours=2)).isoformat()
    async with rig.client() as client:
        response = await client.post(
            "/traces", json=[rec(1, ts=future), rec(2, ts=soon), rec(3, ts=now.isoformat())]
        )
    body = response.json()
    assert body["accepted"] == 2
    assert [r["index"] for r in body["rejected"]] == [0]
    assert "future" in body["rejected"][0]["reason"]


async def test_twenty_concurrent_identical_batches_store_each_record_once(rig: Rig) -> None:
    batch = [rec(i) for i in range(1, 31)]
    async with rig.client() as client:
        results = await asyncio.gather(*[client.post("/traces", json=batch) for _ in range(20)])
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    assert all(r.status_code == 200 for r in results)
    assert sum(r.json()["accepted"] for r in results) == 30
    assert len(body["records"]) == 30
    assert len((rig.dir / "run_1.jsonl").read_text().splitlines()) == 30


async def test_concurrent_batches_cannot_overshoot_the_record_cap(tmp_path: Path) -> None:
    small = Rig(tmp_path, max_run_records=50)
    async with small.client() as client:
        results = await asyncio.gather(
            *[
                client.post("/traces", json=[rec(n * 100 + i) for i in range(1, 11)])
                for n in range(10)
            ]
        )
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    assert sum(r.json()["accepted"] for r in results) == 50
    assert len(body["records"]) == 50
