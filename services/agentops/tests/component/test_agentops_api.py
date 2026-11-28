import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from svc_agentops.testing import T0, Rig, rec

pytestmark = pytest.mark.asyncio


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def at(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


async def test_ingest_requires_the_internal_token(rig: Rig) -> None:
    async with rig.client() as client:
        client.headers.clear()
        assert (await client.post("/traces", json=rec())).status_code == 401
        assert (await client.get("/runs")).status_code == 401
        assert (await client.get("/runs/run_1/trace")).status_code == 401
        client.headers["X-Internal-Token"] = "wrong"
        assert (await client.post("/traces", json=rec())).status_code == 401


async def test_a_single_record_and_a_batch_are_accepted(rig: Rig) -> None:
    async with rig.client() as client:
        one = await client.post("/traces", json=rec(1))
        batch = await client.post("/traces", json=[rec(2), rec(3)])
    assert one.json() == {"accepted": 1, "duplicates": 0, "rejected": []}
    assert batch.json()["accepted"] == 2
    assert len(rig.index.rows) == 3


async def test_ingest_is_idempotent_across_and_within_batches(rig: Rig) -> None:
    async with rig.client() as client:
        first = await client.post("/traces", json=[rec(1), rec(1), rec(2)])
        again = await client.post("/traces", json=[rec(1), rec(2)])
        trace = await client.get("/runs/run_1/trace?flat=true")
    assert first.json()["accepted"] == 2
    assert first.json()["duplicates"] == 1
    assert again.json() == {"accepted": 0, "duplicates": 2, "rejected": []}
    assert len(trace.json()["records"]) == 2
    assert len(rig.index.rows) == 2


async def test_a_batch_of_100_is_ok_and_101_is_refused_with_the_error_envelope(rig: Rig) -> None:
    async with rig.client() as client:
        ok = await client.post("/traces", json=[rec(i) for i in range(1, 101)])
        too_many = await client.post("/traces", json=[rec(i) for i in range(101, 202)])
    assert ok.json()["accepted"] == 100
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(rig.index.rows) == 100  # nothing from the refused batch


@pytest.mark.parametrize("body", [[], "text", 5, None])
async def test_empty_and_non_record_bodies_are_422(rig: Rig, body: Any) -> None:
    async with rig.client() as client:
        response = await client.post("/traces", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_malformed_json_is_422_not_500(rig: Rig) -> None:
    async with rig.client() as client:
        response = await client.post(
            "/traces", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        binary = await client.post("/traces", content=b"\xff\xfe\x00")
    assert response.status_code == 422
    assert binary.status_code == 422


async def test_bad_records_are_rejected_one_by_one_and_good_ones_still_stored(rig: Rig) -> None:
    bad_schema = rec(2)
    del bad_schema["agent"]
    extra = rec(3, surprise=1)
    async with rig.client() as client:
        response = await client.post(
            "/traces",
            json=[rec(1), bad_schema, extra, rec(4, run_id="../evil"), rec(5, cost_usd=-1), "x"],
        )
    body = response.json()
    assert response.status_code == 200
    assert body["accepted"] == 1
    assert sorted(r["index"] for r in body["rejected"]) == [1, 2, 3, 4, 5]
    assert not list(rig.dir.glob("*evil*"))
    assert [p.name for p in rig.dir.iterdir()] == ["run_1.jsonl"]


async def test_a_huge_body_is_413_and_a_huge_record_is_truncated_not_stored_whole(
    rig: Rig,
) -> None:
    async with rig.client() as client:
        huge = await client.post("/traces", content=b"[" + b"0," * 3_000_000 + b"0]")
        big_payload = await client.post("/traces", json=rec(1, input={"a": "x" * 1_000_000}))
        trace = await client.get("/runs/run_1/trace?flat=true")
    assert huge.status_code == 413
    assert big_payload.json()["accepted"] == 1
    assert trace.json()["records"][0]["input"]["_truncated"] is True
    assert (rig.dir / "run_1.jsonl").stat().st_size < 20_000


async def test_secrets_and_pii_are_not_stored(rig: Rig) -> None:
    async with rig.client() as client:
        await client.post(
            "/traces",
            json=rec(
                1,
                input={"token": "abc", "text": "ping me at jane@example.com"},
                error="Bearer abcdef.ghijkl",
            ),
        )
    stored = (rig.dir / "run_1.jsonl").read_text()
    for leak in ("jane@example.com", "abcdef.ghijkl", '"abc"'):
        assert leak not in stored


async def test_the_trace_is_an_ordered_tree_with_totals(rig: Rig) -> None:
    records = [
        rec(2, parent_step=1, agent="validators", tokens_in=100, cost_usd=0.5, ts=at(5)),
        rec(3, parent_step=1, agent="validators", tokens_in=50, cost_usd=0.25, ts=at(7)),
        rec(1, agent="orchestrator", ts=at(10), latency_ms=9000, tokens_in=0, cost_usd=0),
    ]
    async with rig.client() as client:
        await client.post("/traces", json=records)  # arrival order: children first
        response = await client.get("/runs/run_1/trace")
    body = response.json()
    assert response.status_code == 200
    (root,) = body["tree"]
    assert root["record"]["step_id"] == 1
    assert [c["record"]["step_id"] for c in root["children"]] == [2, 3]
    assert body["totals"]["steps"] == 3
    assert body["totals"]["tokens_in"] == 150
    assert body["totals"]["cost_usd"] == 0.75
    assert body["by_agent"]["validators"]["steps"] == 2
    assert body["by_agent"]["validators"]["cost_usd"] == 0.75
    assert body["truncated"] is False


async def test_flat_returns_the_ordered_raw_records(rig: Rig) -> None:
    async with rig.client() as client:
        await client.post("/traces", json=[rec(2, ts=at(9)), rec(1, ts=at(3))])
        body = (await client.get("/runs/run_1/trace", params={"flat": "true"})).json()
    assert [r["step_id"] for r in body["records"]] == [1, 2]
    assert "tree" not in body


async def test_unknown_run_is_404_and_traversal_attempts_are_422_or_404(rig: Rig) -> None:
    async with rig.client() as client:
        unknown = await client.get("/runs/run_nope/trace")
        dots = await client.get("/runs/..%2f..%2fetc%2fpasswd/trace")
        dotted = await client.get("/runs/run.1/trace")
        long = await client.get(f"/runs/{'a' * 65}/trace")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "NOT_FOUND"
    assert dots.status_code in (404, 422)
    assert dotted.status_code == 422
    assert long.status_code == 422


async def test_concurrent_writers_to_one_run_lose_nothing(rig: Rig) -> None:
    async with rig.client() as client:
        results = await asyncio.gather(
            *[
                client.post("/traces", json=[rec(n * 10 + i) for i in range(1, 11)])
                for n in range(10)
            ]
        )
        body = (await client.get("/runs/run_1/trace?flat=true")).json()
    assert all(r.status_code == 200 for r in results)
    assert len(body["records"]) == 100
    lines = (rig.dir / "run_1.jsonl").read_text().splitlines()
    assert len(lines) == 100


async def test_runs_are_listed_newest_first_with_stable_paging(rig: Rig) -> None:
    async with rig.client() as client:
        for n in range(1, 8):
            await client.post("/traces", json=rec(1, run_id=f"run_{n}", ts=at(n * 100)))
        # two runs that started in the same instant: paging must not skip or repeat either
        await client.post("/traces", json=rec(1, run_id="run_tie_a", ts=at(300)))
        await client.post("/traces", json=rec(1, run_id="run_tie_b", ts=at(300)))
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 3}
            if cursor:
                params["after"] = cursor
            page = (await client.get("/runs", params=params)).json()
            seen += [r["run_id"] for r in page["runs"]]
            cursor = page["next"]
            if cursor is None:
                break
    assert sorted(seen) == sorted([f"run_{n}" for n in range(1, 8)] + ["run_tie_a", "run_tie_b"])
    assert len(seen) == len(set(seen))
    assert seen[0] == "run_7"
    assert seen[-1] == "run_1"


async def test_runs_summaries_aggregate_the_index(rig: Rig) -> None:
    async with rig.client() as client:
        await client.post(
            "/traces", json=[rec(1, cost_usd=0.5), rec(2, error="boom", cost_usd=0.25)]
        )
        (run,) = (await client.get("/runs")).json()["runs"]
    assert run["steps"] == 2
    assert run["errors"] == 1
    assert run["cost_usd"] == 0.75
    assert run["tokens_in"] == 20


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "limit=-1", "limit=abc", "after=%%%", "after=bm9wZQ"]
)
async def test_runs_paging_inputs_are_validated(rig: Rig, query: str) -> None:
    async with rig.client() as client:
        response = await client.get(f"/runs?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_an_empty_store_lists_no_runs(rig: Rig) -> None:
    async with rig.client() as client:
        assert (await client.get("/runs")).json() == {"runs": [], "next": None}


async def test_a_failing_file_write_leaves_nothing_half_stored(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from svc_agentops.repositories.files import JsonlTraceFiles

    def boom(self: JsonlTraceFiles, run_id: str, items: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(JsonlTraceFiles, "append", boom)
    transport = httpx.ASGITransport(app=rig.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t", headers=rig.headers
    ) as client:
        response = await client.post("/traces", json=[rec(1)])
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"
    assert rig.index.rows == {}


async def test_ready_reflects_the_database_and_the_directory(rig: Rig, tmp_path: Path) -> None:
    async with rig.client() as client:
        assert (await client.get("/ready")).status_code == 200
        rig.index.up = False
        assert (await client.get("/ready")).status_code == 503
