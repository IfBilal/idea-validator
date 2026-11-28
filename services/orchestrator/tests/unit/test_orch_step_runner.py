"""StepRunner: budget pre-flight, memoisation, timeout, tracing and events around one step."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from common.budget import BudgetLedger, BudgetLimits, Spend
from common.errors import BudgetExceeded, NotFound, UpstreamError, UpstreamTimeout
from common.schemas import TraceRecord
from common.tracing import Tracer
from svc_orchestrator.clients.base import retries_var
from svc_orchestrator.repositories.events import InMemoryPublisher, RunEmitter
from svc_orchestrator.services.cache_keys import canonical_hash
from svc_orchestrator.services.run_config import RunConfig, run_config_from
from svc_orchestrator.services.steps import StepRunner
from svc_orchestrator.testing import FakeRedis

REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"


class Rig:
    def __init__(self, *, max_steps: int = 10, max_retries: int = 2) -> None:
        self.redis = FakeRedis()
        limits = BudgetLimits(max_tokens=1000, max_pages=10, max_steps=max_steps, max_cost_usd=1.0)
        self.ledger = BudgetLedger(self.redis, "run_1", limits)
        self.records: list[TraceRecord] = []
        self.publisher = InMemoryPublisher()
        base = run_config_from(
            {
                "validate": {
                    "max_retries": max_retries,
                    "max_tokens": 1000,
                    "max_pages": 10,
                    "max_steps": max_steps,
                    "max_cost_usd": 1.0,
                }
            },
            "validate",
        )
        self.config: RunConfig = base
        self.runner = StepRunner(
            run_id="run_1",
            config=base,
            ledger=self.ledger,
            tracer=Tracer("orchestrator", sink=self.records.append),
            emitter=RunEmitter("run_1", self.publisher),
        )


async def ok(value: Any = None) -> Any:
    return {"ok": True} if value is None else value


async def test_a_step_returns_its_value_and_a_memo_entry() -> None:
    rig = Rig()
    outcome = await rig.runner.run(
        {}, "kb", agent="orchestrator", tool="kb.search", inputs={"q": 1}, call=ok, timeout_s=1
    )
    assert outcome.value == {"ok": True}
    assert outcome.cache_hit is False
    assert list(outcome.memo_update) == [outcome.memo_key]
    assert outcome.memo_update[outcome.memo_key] == {"ok": True}


async def test_a_memo_hit_skips_the_call_and_spends_nothing() -> None:
    rig = Rig()
    first = await rig.runner.run(
        {}, "kb", agent="a", tool="t", inputs={"q": 1}, call=ok, timeout_s=1
    )
    calls = 0

    async def boom() -> Any:
        nonlocal calls
        calls += 1
        return {"fresh": True}

    again = await rig.runner.run(
        first.memo_update, "kb", agent="a", tool="t", inputs={"q": 1}, call=boom, timeout_s=1
    )
    assert calls == 0
    assert again.value == {"ok": True}
    assert again.cache_hit is True
    assert again.memo_update == {}
    assert (await rig.ledger.spent()).steps == 1  # only the first run was charged
    assert rig.records[-1].cache_hit is True


async def test_changed_inputs_are_not_a_memo_hit() -> None:
    rig = Rig()
    first = await rig.runner.run(
        {}, "kb", agent="a", tool="t", inputs={"q": 1}, call=ok, timeout_s=1
    )
    other = await rig.runner.run(
        first.memo_update, "kb", agent="a", tool="t", inputs={"q": 2}, call=ok, timeout_s=1
    )
    assert other.cache_hit is False


async def test_a_step_is_charged_one_step_on_success_and_on_failure() -> None:
    rig = Rig()

    async def fail() -> Any:
        raise UpstreamError("down")

    await rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=ok, timeout_s=1)
    with pytest.raises(UpstreamError):
        await rig.runner.run({}, "s", agent="a", tool="t", inputs=2, call=fail, timeout_s=1)
    assert (await rig.ledger.spent()).steps == 2


@pytest.mark.parametrize(("already", "allowed"), [(8, True), (9, True), (10, False)])
async def test_budget_boundary_at_max_steps(already: int, allowed: bool) -> None:
    rig = Rig(max_steps=10)
    await rig.ledger.record(Spend(steps=already))
    attempt = rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=ok, timeout_s=1)
    if allowed:
        await attempt
        assert (await rig.ledger.spent()).steps == already + 1
    else:
        with pytest.raises(BudgetExceeded):
            await attempt
        assert (await rig.ledger.spent()).steps == already  # nothing recorded, call not made


async def test_an_already_over_budget_run_stops_even_on_a_memo_hit() -> None:
    rig = Rig(max_steps=1)
    first = await rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=ok, timeout_s=1)
    await rig.ledger.record(Spend(steps=5))
    with pytest.raises(BudgetExceeded):
        await rig.runner.run(
            first.memo_update, "s", agent="a", tool="t", inputs=1, call=ok, timeout_s=1
        )


async def test_the_call_is_not_made_when_the_estimate_would_exceed_the_budget() -> None:
    rig = Rig()
    called = False

    async def spy() -> Any:
        nonlocal called
        called = True

    with pytest.raises(BudgetExceeded):
        await rig.runner.run(
            {},
            "s",
            agent="a",
            tool="t",
            inputs=1,
            call=spy,
            timeout_s=1,
            estimate=Spend(steps=1, pages=11),
        )
    assert called is False


async def test_the_estimate_is_checked_but_only_the_step_is_charged() -> None:
    # M5 records pages/tokens/cost itself; M3 double-counting them would trip budgets early.
    rig = Rig()
    await rig.runner.run(
        {},
        "scout",
        agent="a",
        tool="t",
        inputs=1,
        call=ok,
        timeout_s=1,
        estimate=Spend(steps=1, pages=4),
    )
    assert await rig.ledger.spent() == Spend(steps=1)


async def test_a_hanging_call_times_out_as_upstream_timeout() -> None:
    rig = Rig(max_retries=0)

    async def hang() -> Any:
        await asyncio.sleep(5)

    with pytest.raises(UpstreamTimeout):
        await rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=hang, timeout_s=0.02)
    assert rig.records[-1].error is not None
    assert "UpstreamTimeout" in rig.records[-1].error


async def test_the_outer_timeout_allows_for_retries_but_is_still_finite() -> None:
    rig = Rig(max_retries=2)
    assert rig.runner.total_timeout(1.0) == pytest.approx(1.0 * 3 + 1.0)
    assert Rig(max_retries=0).runner.total_timeout(2.0) == pytest.approx(3.0)


async def test_trace_record_carries_tool_inputs_retries_and_cost_context() -> None:
    rig = Rig()

    async def retried() -> Any:
        retries_var.set(retries_var.get() + 2)
        return {"ok": 1}

    await rig.runner.run(
        {},
        "kb",
        agent="orchestrator",
        tool="kb.search",
        inputs={"q": "x"},
        call=retried,
        timeout_s=1,
    )
    record = rig.records[-1]
    assert (record.run_id, record.agent, record.kind, record.tool) == (
        "run_1",
        "orchestrator",
        "tool_call",
        "kb.search",
    )
    assert record.input == {"input_hash": canonical_hash({"q": "x"})[:16]}  # no raw idea text
    assert record.retries == 2
    assert record.cache_hit is False
    assert record.error is None


async def test_failed_step_trace_has_the_error_and_the_retry_budget_used() -> None:
    rig = Rig(max_retries=2)

    async def fail() -> Any:
        raise UpstreamTimeout("slow")

    with pytest.raises(UpstreamTimeout):
        await rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=fail, timeout_s=1)
    assert rig.records[-1].retries == 2  # a timeout is retried max_retries times before giving up
    assert "UpstreamTimeout" in (rig.records[-1].error or "")


async def test_a_non_retryable_failure_reports_zero_retries() -> None:
    rig = Rig(max_retries=2)

    async def missing() -> Any:
        raise NotFound("no such profile", {"status": 404})

    with pytest.raises(NotFound):
        await rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=missing, timeout_s=1)
    assert rig.records[-1].retries == 0


async def test_events_are_emitted_for_start_tool_call_spend_and_finish() -> None:
    rig = Rig()
    await rig.runner.run(
        {}, "kb", agent="orchestrator", tool="kb.search", inputs=1, call=ok, timeout_s=1
    )
    types = [e.type for e in rig.publisher.events_of("run_1")]
    assert types == ["agent_started", "tool_call", "spend_update", "agent_finished"]
    spend = rig.publisher.events_of("run_1")[2].payload
    assert spend["spent"]["steps"] == 1
    assert spend["budget"]["max_steps"] == 10


async def test_trace_ids_are_recorded_even_when_the_step_raises() -> None:
    rig = Rig()

    async def fail() -> Any:
        raise UpstreamError("down")

    with pytest.raises(UpstreamError):
        await rig.runner.run({}, "s", agent="a", tool="t", inputs=1, call=fail, timeout_s=1)
    assert len(rig.records) == 1
    assert [e.type for e in rig.publisher.events_of("run_1")][-1] == "agent_finished"


async def test_decision_records_use_kind_decision() -> None:
    rig = Rig()
    rig.runner.decision("route", {"from": "evidence"}, {"to": "features", "reason": "enough"})
    record = rig.records[-1]
    assert record.kind == "decision"
    assert record.tool == "transition"
    assert record.input == {"from": "evidence"}
    assert record.output == {"decision": "route", "to": "features", "reason": "enough"}


async def test_memoise_false_always_calls_and_never_returns_a_memo_entry() -> None:
    rig = Rig()
    calls = 0

    async def counted() -> Any:
        nonlocal calls
        calls += 1
        return {"n": calls}

    first = await rig.runner.run(
        {}, "profile", agent="a", tool="t", inputs=1, call=counted, timeout_s=1, memoise=False
    )
    assert first.memo_update == {}
    again = await rig.runner.run(
        {first.memo_key: {"n": 99}},
        "profile",
        agent="a",
        tool="t",
        inputs=1,
        call=counted,
        timeout_s=1,
        memoise=False,
    )
    assert again.value == {"n": 2}
    assert again.cache_hit is False


async def test_parallel_steps_cannot_overshoot_the_step_budget() -> None:
    """Four parallel evidence searches used to pass a separate check and end with 7 steps for a cap of 4."""
    rig = Rig(max_steps=2)
    calls: list[int] = []

    def make(i: int) -> Any:
        async def call() -> Any:
            calls.append(i)
            await asyncio.sleep(0)  # yield so the others reach the budget check meanwhile
            return {"i": i}

        return call

    async def attempt(i: int) -> bool:
        try:
            await rig.runner.run(
                {}, f"s{i}", agent="a", tool="t", inputs={"i": i}, call=make(i), timeout_s=1
            )
            return True
        except BudgetExceeded:
            return False

    results = await asyncio.gather(*(attempt(i) for i in range(5)))
    assert sum(results) == 2, f"{sum(results)} steps ran for a budget of 2"
    assert len(calls) == 2, "a refused step must never make its call"
    assert (await rig.ledger.spent()).steps == 2


async def test_a_step_that_returns_idea_text_leaves_none_of_it_in_the_trace() -> None:
    import json

    rig = Rig()
    secret_idea = "my startup idea: invoices for dentists, contact dr.smith"
    result = {
        "idea": secret_idea,
        "reasons": [secret_idea, "quote: " + secret_idea],
        "evidence": [{"evidence_id": "card_aaaaaaaa#c1", "quote": secret_idea}],
        "score": 3.5,
        "n_sources": 4,
        "cache_hit": False,
    }

    async def returns() -> Any:
        return result

    await rig.runner.run({}, "x", tool="t", inputs={"q": 1}, call=returns, timeout_s=1)
    text = json.dumps(rig.records[-1].model_dump(mode="json"))
    assert "dentists" not in text
    assert "smith" not in text
    out = rig.records[-1].output
    assert out["numbers"]["score"] == 3.5
    assert out["counts"]["evidence"] == 1
    assert out["counts"]["reasons"] == 2
    assert out["hash"]


async def test_a_memo_hit_is_summarised_too() -> None:
    import json

    rig = Rig()
    key = (
        "x:"
        + __import__("svc_orchestrator.services.cache_keys", fromlist=["x"]).canonical_hash(
            {"q": 1}
        )[:16]
    )
    memo = {key: {"idea": "secret dentist idea"}}
    await rig.runner.run(memo, "x", tool="t", inputs={"q": 1}, call=ok, timeout_s=1)
    assert "dentist" not in json.dumps(rig.records[-1].model_dump(mode="json"))
