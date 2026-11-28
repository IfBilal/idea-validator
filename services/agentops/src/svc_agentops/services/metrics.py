"""Prometheus metrics derived from newly stored trace records (docs/harness/12 section 7).

Only records that were new in the store are observed, so a retried batch never counts twice.
Label values are bounded (a safe charset, 48 characters, else ``other``) so a hostile or buggy
emitter cannot blow up the number of series.
"""

import re
from collections.abc import Iterable

from prometheus_client import CollectorRegistry, Counter, Histogram

from common.schemas.trace import TraceRecord

_SAFE = re.compile(r"[A-Za-z0-9_.:@/-]{1,48}")
VERDICTS = frozenset({"GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"})
DECISION = "m11_verdict"
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120)


def label(value: str | None, default: str = "none") -> str:
    if value is None:
        return default
    return value if _SAFE.fullmatch(value) else "other"


class TraceMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.latency = Histogram(
            "agent_step_latency_seconds",
            "Latency of one traced step",
            ["agent", "kind"],
            buckets=LATENCY_BUCKETS,
            registry=registry,
        )
        self.tokens = Counter(
            "llm_tokens_total",
            "Tokens of traced model calls",
            ["agent", "model", "direction"],
            registry=registry,
        )
        self.cost = Counter(
            "trace_cost_usd_total", "Cost of traced steps in USD", ["agent"], registry=registry
        )
        self.cache = Counter(
            "cache_requests_total",
            "Traced tool and model calls by cache result (kind is the cache label)",
            ["cache", "result"],
            registry=registry,
        )
        self.fallbacks = Counter(
            "scorer_fallback_total", "Steps that used a fallback", ["agent"], registry=registry
        )
        self.errors = Counter(
            "trace_errors_total", "Traced steps that ended in error", ["agent"], registry=registry
        )
        self.verdicts = Counter(
            "verdicts_total",
            "Verdicts M11 gave (m11_verdict decisions)",
            ["verdict"],
            registry=registry,
        )

    def observe(self, records: Iterable[TraceRecord]) -> None:
        for r in records:
            agent = label(r.agent)
            self.latency.labels(agent, r.kind).observe(max(0, r.latency_ms) / 1000)
            if r.cost_usd > 0:
                self.cost.labels(agent).inc(r.cost_usd)
            if r.error:
                self.errors.labels(agent).inc()
            if r.fallback:
                self.fallbacks.labels(agent).inc()
            if r.kind in ("tool_call", "llm_call"):
                self.cache.labels(r.kind, "hit" if r.cache_hit else "miss").inc()
            if r.kind == "llm_call":
                model = label(r.model, "unknown")
                if r.tokens_in > 0:
                    self.tokens.labels(agent, model, "in").inc(r.tokens_in)
                if r.tokens_out > 0:
                    self.tokens.labels(agent, model, "out").inc(r.tokens_out)
            if r.kind == "decision" and isinstance(r.output, dict):
                verdict = r.output.get("verdict")
                if r.output.get("decision") == DECISION and verdict in VERDICTS:
                    self.verdicts.labels(verdict).inc()
