"""POST /drift and GET /drift/latest, plus the drift CLI end to end on a trace directory."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentops import drift
from svc_agentops.repositories.drift_store import DriftStore
from svc_agentops.repositories.files import JsonlTraceFiles
from svc_agentops.testing import Rig

NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)


def report(at: datetime = NOW) -> Any:
    stats = {"cards": 3, "by_source": {"a": 3}, "last_ingested_at": {"a": "2026-11-27"}}
    return drift.build_report(now=at, stats=stats, snapshot=None, verdicts=[])


@pytest.mark.asyncio
async def test_drift_requires_the_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    async with rig.client() as client:
        client.headers.clear()
        assert (await client.get("/drift/latest")).status_code == 401
        assert (await client.post("/drift", content=b"{}")).status_code == 401


@pytest.mark.asyncio
async def test_no_report_yet_is_404(tmp_path: Path) -> None:
    async with Rig(tmp_path).client() as client:
        assert (await client.get("/drift/latest")).status_code == 404


@pytest.mark.asyncio
async def test_post_then_latest_returns_the_newest(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    async with rig.client() as client:
        for hours in (0, 2, 1):
            body = report(NOW + timedelta(hours=hours)).model_dump_json()
            assert (await client.post("/drift", content=body)).status_code == 200
        latest = (await client.get("/drift/latest")).json()
    assert latest["generated_at"].startswith((NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"))
    assert len(list((rig.dir / "drift").glob("drift-*.json"))) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"schema_version": 2}', b"[]"])
async def test_bad_bodies_are_422(tmp_path: Path, body: bytes) -> None:
    async with Rig(tmp_path).client() as client:
        response = await client.post("/drift", content=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_an_oversized_report_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path, max_drift_bytes=100)
    async with rig.client() as client:
        assert (await client.post("/drift", content=report().model_dump_json())).status_code == 422


@pytest.mark.asyncio
async def test_a_corrupt_newest_file_falls_back_to_the_older_report(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    async with rig.client() as client:
        await client.post("/drift", content=report().model_dump_json())
        (rig.dir / "drift" / "drift-20991231T000000Z.json").write_text("{broken")
        response = await client.get("/drift/latest")
    assert response.status_code == 200


def test_old_reports_are_pruned(tmp_path: Path) -> None:
    store = DriftStore(tmp_path / "d", keep=3)
    for n in range(10):
        store.save(report(NOW + timedelta(minutes=n)))
    assert len(list((tmp_path / "d").glob("drift-*.json"))) == 3
    latest = store.latest()
    assert latest is not None and latest.generated_at == NOW + timedelta(minutes=9)


# ---- CLI -----------------------------------------------------------------------------------------
def write_run(directory: Path, run: str, verdict: str, at: datetime) -> None:
    record = {
        "run_id": run,
        "step_id": 1,
        "kind": "decision",
        "ts": at.isoformat(),
        "output": {"decision": "m11_verdict", "verdict": verdict},
    }
    JsonlTraceFiles(directory).append(run, [(f"{run}-1", record)])


def test_cli_writes_a_report_from_traces_and_stats(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    traces, out = tmp_path / "traces", tmp_path / "out"
    traces.mkdir()
    now = datetime.now(UTC)
    for i in range(6):
        write_run(traces, f"old{i}", "GO", now - timedelta(hours=10 + i))
        write_run(traces, f"new{i}", "NO-GO", now - timedelta(minutes=i))
    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps({"cards": 2, "by_source": {"a": 2}, "last_ingested_at": {"a": "2020-01-01"}})
    )
    argv = ["--trace-dir", str(traces), "--stats-file", str(stats), "--out-dir", str(out)]
    code = drift.main([*argv, "--window", "6", "--strict"])
    assert code == 1  # strict and flags raised
    written = list(out.glob("drift-*.json"))
    assert len(written) == 1
    data = json.loads(written[0].read_text())
    assert "verdict_shift" in data["flags"] and "stale:a" in data["flags"]
    assert json.loads(capsys.readouterr().out)["flags"] == data["flags"]


def test_cli_without_strict_exits_zero_even_with_flags(tmp_path: Path) -> None:
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"by_source": {"a": 1}, "last_ingested_at": {}}))
    assert drift.main(["--stats-file", str(stats)]) == 0


def test_cli_with_no_input_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert drift.main(["--trace-dir", str(tmp_path)]) == 2
    assert "no usable input" in capsys.readouterr().err


def test_cli_rejects_bad_arguments(tmp_path: Path) -> None:
    assert drift.main(["--window", "0", "--stats-file", str(tmp_path / "x")]) == 2
    assert drift.main(["--stats-file", str(tmp_path / "missing.json")]) == 2


def test_cli_second_run_uses_the_previous_report_for_coverage(tmp_path: Path) -> None:
    out, stats = tmp_path / "out", tmp_path / "stats.json"
    stats.write_text(json.dumps({"by_source": {"a": 5}, "last_ingested_at": {}}))
    assert drift.main(["--stats-file", str(stats), "--out-dir", str(out)]) == 0
    next(out.glob("drift-*.json")).rename(out / "drift-20200101T000000Z.json")
    assert drift.main(["--stats-file", str(stats), "--out-dir", str(out)]) == 0
    data = json.loads(sorted(out.glob("drift-*.json"))[-1].read_text())
    assert data["coverage"]["basis"] == "stats_delta"
    assert "no_new_cards:a" in data["flags"]


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [-3, 3, 3650])
async def test_a_report_dated_far_from_now_is_refused(tmp_path: Path, days: int) -> None:
    rig = Rig(tmp_path)
    body = report(datetime.now(UTC) + timedelta(days=days)).model_dump_json()
    async with rig.client() as client:
        response = await client.post("/drift", content=body)
        latest = await client.get("/drift/latest")
    assert response.status_code == 422
    assert latest.status_code == 404  # a bad clock can never shadow later reports


@pytest.mark.asyncio
async def test_a_report_within_a_day_of_now_is_accepted(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    async with rig.client() as client:
        for hours in (-20, 20):
            body = report(datetime.now(UTC) + timedelta(hours=hours)).model_dump_json()
            assert (await client.post("/drift", content=body)).status_code == 200
