"""/metrics exposes per-agent latency, tokens, cost, cache, fallback and verdict series."""

from pathlib import Path
from typing import Any

import pytest

from svc_agentops.testing import Rig, rec


async def scrape(rig: Rig) -> str:
    async with rig.client() as client:
        return (await client.get("/metrics")).text


def sample(text: str, name: str, **labels: str) -> float:
    wanted = {f'{k}="{v}"' for k, v in labels.items()}
    for line in text.splitlines():
        if line.startswith(name + "{") and wanted <= set(
            line.split("{")[1].split("}")[0].split(",")
        ):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


@pytest.mark.asyncio
async def test_ingested_records_feed_the_series(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    decision: dict[str, Any] = rec(
        4,
        kind="decision",
        agent="verdict",
        output={"decision": "m11_verdict", "verdict": "GO"},
    )
    batch = [
        rec(
            1,
            agent="scorer",
            kind="llm_call",
            model="m-1",
            tokens_in=100,
            tokens_out=40,
            cost_usd=0.002,
            latency_ms=1500,
        ),
        rec(
            2,
            agent="scorer",
            kind="llm_call",
            model="m-1",
            cache_hit=True,
            fallback=True,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
        ),
        rec(3, agent="scouts", kind="tool_call", error="boom"),
        decision,
    ]
    async with rig.client() as client:
        assert (await client.post("/traces", json=batch)).json()["accepted"] == 4
        text = (await client.get("/metrics")).text
    assert sample(text, "llm_tokens_total", agent="scorer", model="m-1", direction="in") == 100
    assert sample(text, "llm_tokens_total", agent="scorer", direction="out") == 40
    assert sample(text, "cache_requests_total", cache="llm_call", result="hit") == 1
    assert sample(text, "cache_requests_total", cache="llm_call", result="miss") == 1
    assert sample(text, "scorer_fallback_total", agent="scorer") == 1
    assert sample(text, "trace_errors_total", agent="scouts") == 1
    assert sample(text, "verdicts_total", verdict="GO") == 1
    assert sample(text, "trace_cost_usd_total", agent="scorer") == pytest.approx(0.002)
    assert sample(text, "agent_step_latency_seconds_bucket", agent="scorer", le="2.0") >= 1
    assert sample(text, "agent_step_latency_seconds_count", agent="scorer", kind="llm_call") == 2


@pytest.mark.asyncio
async def test_a_retried_batch_is_not_counted_twice(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    batch = [rec(1, agent="a", kind="llm_call", model="m", tokens_in=7)]
    async with rig.client() as client:
        await client.post("/traces", json=batch)
        await client.post("/traces", json=batch)
        text = (await client.get("/metrics")).text
    assert sample(text, "llm_tokens_total", agent="a", direction="in") == 7


@pytest.mark.asyncio
async def test_unsafe_label_values_collapse_to_other(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    evil = "bad agent! fake_metric{a=1}"
    async with rig.client() as client:
        r = await client.post("/traces", json=[rec(1, agent=evil, kind="llm_call", model="y" * 20)])
        text = (await client.get("/metrics")).text
    assert r.json()["accepted"] == 1, r.text
    assert "fake_metric" not in text
    assert sample(text, "agent_step_latency_seconds_count", agent="other") == 1


@pytest.mark.asyncio
async def test_decisions_that_are_not_verdicts_are_ignored(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    out = {"decision": "m11_verdict", "verdict": "MAYBE"}
    async with rig.client() as client:
        await client.post("/traces", json=[rec(1, kind="decision", output=out)])
        await client.post(
            "/traces",
            json=[rec(2, kind="decision", output={"decision": "x", "verdict": "GO"})],
        )
        text = (await client.get("/metrics")).text
    assert "verdicts_total{" not in text


@pytest.mark.asyncio
async def test_stress_many_records_keep_series_bounded(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    async with rig.client() as client:
        for start in range(1, 401, 100):
            batch = [rec(i, agent=f"a{i % 5}", kind="tool_call") for i in range(start, start + 100)]
            await client.post("/traces", json=batch)
        text = (await client.get("/metrics")).text
    assert (
        sum(1 for line in text.splitlines() if line.startswith("agent_step_latency_seconds_count"))
        == 5
    )


def registry_copy(tmp_path: Path) -> Path:
    import shutil

    root = tmp_path / "reg"
    shutil.copytree(Path(__file__).resolve().parents[4] / "config", root / "config")
    return root


@pytest.mark.asyncio
async def test_a_thousand_distinct_agent_names_stay_bounded(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    async with rig.client() as client:
        for start in range(0, 1000, 100):
            batch = [rec(i + 1, agent=f"agent{start + i}") for i in range(100)]
            await client.post("/traces", json=batch)
        text = (await client.get("/metrics")).text
    series = [ln for ln in text.splitlines() if ln.startswith("agent_step_latency_seconds_count")]
    assert 0 < len(series) <= 65  # 64 distinct values, the rest folded into "other"
    assert sum(float(ln.rsplit(" ", 1)[1]) for ln in series) == 1000


@pytest.mark.asyncio
async def test_unknown_agents_and_models_fold_into_other_when_config_is_known(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, registry_dir=registry_copy(tmp_path))
    batch = [
        rec(1, agent="scorer", kind="llm_call", model="openai/gpt-oss-120b", tokens_in=5),
        rec(2, agent="made-up-agent", kind="llm_call", model="made-up-model", tokens_in=7),
    ]
    async with rig.client() as client:
        await client.post("/traces", json=batch)
        text = (await client.get("/metrics")).text
    assert sample(text, "llm_tokens_total", agent="scorer", model="openai/gpt-oss-120b") == 5
    assert sample(text, "llm_tokens_total", agent="other", model="other", direction="in") == 7
    assert "made-up" not in text


@pytest.mark.asyncio
async def test_a_failing_metrics_observer_never_fails_a_stored_batch(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def boom(_: Any) -> None:
        raise RuntimeError("metrics broke")

    rig.app.state.ingest._metrics.observe = boom
    async with rig.client() as client:
        response = await client.post("/traces", json=[rec(1)])
        trace = await client.get("/runs/run_1/trace?flat=true")
    assert response.status_code == 200 and response.json()["accepted"] == 1
    assert len(trace.json()["records"]) == 1
