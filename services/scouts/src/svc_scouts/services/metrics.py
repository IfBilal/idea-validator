"""Prometheus metrics of the scouts (served by the app's ``/metrics`` registry)."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class ScoutMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.runs = Counter(
            "scout_runs_total", "Scout runs", ["source", "status"], registry=self.registry
        )
        self.items = Counter(
            "scout_items_total",
            "Raw items by outcome",
            ["source", "outcome"],  # added | updated | unchanged | dropped
            registry=self.registry,
        )
        self.requests = Counter(
            "scout_requests_total", "M5 fetch calls", ["source", "outcome"], registry=self.registry
        )
        self.flagged = Counter(
            "scout_flagged_pages_total",
            "Pages where M5 removed instruction-like text",
            ["source"],
            registry=self.registry,
        )
        self.up = Gauge(
            "scout_source_up",
            "1 unless the circuit breaker is open for the source",
            ["source"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "scout_run_duration_seconds", "Run duration", ["source"], registry=self.registry
        )
