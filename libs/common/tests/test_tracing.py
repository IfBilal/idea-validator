import pytest

from common.schemas import TraceRecord
from common.tracing import MAX_PAYLOAD_CHARS, Tracer


def make() -> tuple[Tracer, list[TraceRecord]]:
    records: list[TraceRecord] = []
    return Tracer("orchestrator", sink=records.append), records


def test_span_emits_a_valid_record_with_usage() -> None:
    tracer, records = make()
    with tracer.span("scorer", "llm_call", run_id="run_1", model="m", input={"idea": "x"}) as s:
        s.set_output({"score": 3})
        s.set_usage(tokens_in=10, tokens_out=5, cost_usd=0.001)
    (rec,) = records
    assert (rec.run_id, rec.step_id, rec.parent_step, rec.kind) == ("run_1", 1, None, "llm_call")
    assert rec.tokens_in == 10
    assert rec.error is None
    assert rec.service == "orchestrator"


def test_nested_spans_link_parent_steps_and_step_ids_increase() -> None:
    tracer, records = make()
    with (
        tracer.span("m3", "decision", run_id="r"),
        tracer.span("m9", "tool_call", run_id="r", tool="search"),
    ):
        pass
    inner, outer = records
    assert (outer.step_id, outer.parent_step) == (1, None)
    assert (inner.step_id, inner.parent_step) == (2, 1)


def test_errors_are_recorded_and_reraised() -> None:
    tracer, records = make()
    with pytest.raises(RuntimeError), tracer.span("a", "tool_call", run_id="r"):
        raise RuntimeError("boom")
    assert records[0].error == "RuntimeError: boom"


def test_secrets_are_redacted_and_big_payloads_truncated() -> None:
    tracer, records = make()
    with tracer.span("a", "tool_call", run_id="r", input={"api_key": "k", "text": "x" * 10000}):
        pass
    payload = records[0].input
    assert payload["_truncated"] is True
    assert len(payload["preview"]) <= MAX_PAYLOAD_CHARS
    assert "api_key" not in payload["preview"] or '"[REDACTED]"' in payload["preview"]


def test_sink_failure_never_breaks_the_run() -> None:
    def broken(_: TraceRecord) -> None:
        raise OSError("m13 down")

    tracer = Tracer("x", sink=broken)
    with tracer.span("a", "decision", run_id="r") as s:
        s.set_output("fine")


def test_run_id_is_required() -> None:
    tracer, _ = make()
    with pytest.raises(ValueError, match="run_id"), tracer.span("a", "decision"):
        pass


def test_the_per_run_step_counters_are_bounded_so_a_long_lived_service_cannot_leak() -> None:
    records: list[TraceRecord] = []
    tracer = Tracer("tools", sink=records.append, max_runs=3)
    for i in range(50):  # 50 distinct run ids, as a busy M5 sees over its lifetime
        with tracer.span("scorer", "llm_call", run_id=f"run_{i}"):
            pass
    assert tracer.tracked_runs <= 3


def test_an_active_run_keeps_counting_after_other_runs_come_and_go() -> None:
    records: list[TraceRecord] = []
    tracer = Tracer("tools", sink=records.append, max_runs=3)
    for _ in range(2):
        with tracer.span("a", "decision", run_id="hot"):
            pass
        with tracer.span("a", "decision", run_id="other_1"):  # touches another run in between
            pass
    with tracer.span("a", "decision", run_id="hot"):
        pass
    assert [r.step_id for r in records if r.run_id == "hot"] == [1, 2, 3]


def test_the_default_bound_is_generous_for_real_use() -> None:
    assert Tracer("x").max_runs >= 10_000
