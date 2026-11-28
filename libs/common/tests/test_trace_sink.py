import json
import threading
import time
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from common.config import ServiceSettings
from common.schemas import TraceRecord
from common.trace_sink import AgentOpsSink, tracer_for
from common.tracing import Tracer


def record(step: int = 1, run_id: str = "run_1") -> TraceRecord:
    return TraceRecord(
        run_id=run_id,
        step_id=step,
        parent_step=None,
        agent="a",
        kind="tool_call",
        tool=None,
        input={},
        output={},
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=1,
        retries=0,
        model=None,
        cache_hit=False,
        error=None,
        ts=datetime.now(UTC),
    )


class Collector:
    """A MockTransport handler that records every batch and can be told to fail."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.batches: list[list[dict[str, object]]] = []
        self.headers: list[httpx.Headers] = []
        self.calls = 0
        self.delay = 0.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        time.sleep(self.delay)
        if self.status == 0:
            raise httpx.ConnectError("down")
        if self.status < 300:
            self.batches.append(json.loads(request.content))
            self.headers.append(request.headers)
        return httpx.Response(self.status, json={})


def sink_with(collector: Collector, **kw: int) -> AgentOpsSink:
    return AgentOpsSink(
        "http://agentops",
        "tok",
        transport=httpx.MockTransport(collector),
        flush_interval_s=0.01,
        retry_delay_s=0.0,
        **kw,
    )


def test_records_are_posted_in_batches_with_the_internal_token() -> None:
    c = Collector()
    sink = sink_with(c)
    for i in range(1, 251):
        sink(record(i))
    sink.close(timeout_s=5)
    assert sum(len(b) for b in c.batches) == 250
    assert all(len(b) <= 100 for b in c.batches)
    assert c.headers[0]["x-internal-token"] == "tok"
    assert sink.sent == 250
    assert sink.dropped == 0


def test_a_full_queue_drops_and_counts_without_blocking() -> None:
    c = Collector()
    c.delay = 0.3
    sink = sink_with(c, max_queue=5)
    started = time.perf_counter()
    for i in range(1, 501):
        sink(record(i))
    assert time.perf_counter() - started < 0.5  # the caller never waits on M13
    assert sink.dropped > 400
    sink.close(timeout_s=5)


def test_an_unreachable_agentops_drops_after_bounded_attempts() -> None:
    c = Collector(status=0)
    sink = sink_with(c, max_attempts=2)
    sink(record())
    sink.close(timeout_s=5)
    assert c.calls == 2
    assert sink.dropped == 1
    assert sink.sent == 0


def test_a_server_error_is_retried_but_a_client_error_is_not() -> None:
    c = Collector(status=503)
    sink = sink_with(c, max_attempts=3)
    sink(record())
    sink.close(timeout_s=5)
    assert c.calls == 3
    c4 = Collector(status=422)
    sink4 = sink_with(c4, max_attempts=3)
    sink4(record())
    sink4.close(timeout_s=5)
    assert c4.calls == 1
    assert sink4.dropped == 1


def test_the_sink_never_raises_into_the_caller_even_after_close() -> None:
    sink = sink_with(Collector())
    sink.close(timeout_s=2)
    sink(record())  # dropped, not raised
    assert sink.dropped == 1


def test_concurrent_producers_lose_nothing_when_the_queue_is_big_enough() -> None:
    c = Collector()
    sink = sink_with(c, max_queue=5000)

    def produce(offset: int) -> None:
        for i in range(200):
            sink(record(offset + i))

    threads = [threading.Thread(target=produce, args=(n * 1000,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sink.close(timeout_s=10)
    assert sum(len(b) for b in c.batches) == 1600


def test_close_is_idempotent_and_flushes_what_is_queued() -> None:
    c = Collector()
    sink = sink_with(c)
    sink(record())
    sink.close(timeout_s=5)
    sink.close(timeout_s=5)
    assert sum(len(b) for b in c.batches) == 1


def test_a_tracer_with_the_sink_ships_its_spans() -> None:
    c = Collector()
    sink = sink_with(c)
    tracer = Tracer("orchestrator", sink=sink)
    with tracer.span("a", "tool_call", run_id="run_9"):
        pass
    sink.close(timeout_s=5)
    assert c.batches[0][0]["run_id"] == "run_9"
    assert c.batches[0][0]["service"] == "orchestrator"


def test_tracer_for_has_no_sink_without_a_url_and_reads_the_token_from_settings() -> None:
    assert tracer_for("orchestrator", ServiceSettings())._sink is None
    settings = ServiceSettings(agentops_url="http://agentops:8000", internal_token=SecretStr("t"))
    tracer = tracer_for("orchestrator", settings)
    assert isinstance(tracer._sink, AgentOpsSink)
    tracer._sink.close(timeout_s=1)
