"""Ship trace records to M13 without ever slowing or breaking a run (docs/harness/12 §1).

``AgentOpsSink`` is a ``Tracer`` sink. Calling it only puts the record on a bounded queue; a
daemon thread batches records (at most 100, or whatever arrived within the flush interval) and
posts them to ``POST <AGENTOPS_URL>/traces``. When the queue is full, or M13 stays unreachable
after a few attempts, records are dropped and counted (``dropped``), never buffered without bound
and never raised.
"""

import atexit
import queue
import threading
import time
from typing import Any

import httpx

from .config import ServiceSettings
from .context import INTERNAL_TOKEN_HEADER
from .logging import get_logger
from .schemas.trace import TraceRecord
from .tracing import Tracer

MAX_BATCH = 100  # M13's ingest limit
_LOG_EVERY = 100  # warn on the first drop and then once per this many


class AgentOpsSink:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        max_queue: int = 1000,
        batch_size: int = MAX_BATCH,
        flush_interval_s: float = 0.5,
        timeout_s: float = 2.0,
        max_attempts: int = 2,
        retry_delay_s: float = 0.2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/traces"
        self._headers = {INTERNAL_TOKEN_HEADER: token} if token else {}
        self._queue: queue.Queue[TraceRecord] = queue.Queue(maxsize=max_queue)
        self._batch_size = min(batch_size, MAX_BATCH)
        self._flush_interval_s = flush_interval_s
        self._max_attempts = max(1, max_attempts)
        self._retry_delay_s = retry_delay_s
        self._client = httpx.Client(timeout=timeout_s, transport=transport)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.sent = 0
        self.dropped = 0
        self._log = get_logger("trace_sink")
        self._thread = threading.Thread(target=self._run, name="trace-sink", daemon=True)
        self._thread.start()

    def __call__(self, record: TraceRecord) -> None:
        if self._stop.is_set():
            self._drop(1, "closed")
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._drop(1, "queue full")

    def close(self, timeout_s: float = 2.0) -> None:
        """Flush what is queued (bounded by ``timeout_s``) and stop the worker."""
        self._stop.set()
        self._thread.join(timeout_s)
        self._client.close()

    def _drop(self, count: int, reason: str) -> None:
        with self._lock:
            before = self.dropped
            self.dropped += count
            crossed = before == 0 or before // _LOG_EVERY != self.dropped // _LOG_EVERY
        if crossed:
            self._log.warning("trace_records_dropped", reason=reason, dropped_total=self.dropped)

    def _run(self) -> None:
        while True:
            batch = self._collect()
            if batch:
                self._post(batch)
            elif self._stop.is_set():
                return

    def _collect(self) -> list[TraceRecord]:
        batch: list[TraceRecord] = []
        deadline = time.monotonic() + self._flush_interval_s
        while len(batch) < self._batch_size:
            remaining = deadline - time.monotonic()
            try:
                if self._stop.is_set():
                    batch.append(self._queue.get_nowait())
                elif remaining > 0:
                    batch.append(self._queue.get(timeout=remaining))
                else:
                    break
            except queue.Empty:
                break
        return batch

    def _post(self, batch: list[TraceRecord]) -> None:
        body: list[dict[str, Any]] = [r.model_dump(mode="json") for r in batch]
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(self._url, json=body, headers=self._headers)
            except httpx.HTTPError:
                response = None
            if response is not None and response.status_code < 300:
                self.sent += len(batch)
                return
            if response is not None and response.status_code < 500 and response.status_code != 429:
                break  # M13 refused the batch: a retry cannot change that
            if attempt < self._max_attempts:
                time.sleep(self._retry_delay_s)
        self._drop(len(batch), "agentops unavailable or refused the batch")


def tracer_for(service: str, settings_or_url: ServiceSettings | str | None) -> Tracer:
    """A ``Tracer`` that ships to M13 when ``AGENTOPS_URL`` is configured, else stdout only."""
    if isinstance(settings_or_url, ServiceSettings):
        url = settings_or_url.agentops_url
        secret = settings_or_url.internal_token
        token = secret.get_secret_value() if secret else None
    else:
        url, token = settings_or_url, None
    if not url:
        return Tracer(service)
    sink = AgentOpsSink(url, token)
    atexit.register(sink.close)  # flush what is queued when the process stops
    return Tracer(service, sink=sink)
