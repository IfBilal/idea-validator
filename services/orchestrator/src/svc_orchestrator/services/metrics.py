"""Prometheus counters of M3: runs by kind and final status (dashboard panel "Budget stops")."""

from prometheus_client import CollectorRegistry, Counter


class RunMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._runs = Counter(
            "runs_total",
            "Runs that reached a terminal state",
            ["kind", "status"],
            registry=registry,
        )

    def terminal(self, kind: str, status: str) -> None:
        self._runs.labels(kind=kind, status=status).inc()
