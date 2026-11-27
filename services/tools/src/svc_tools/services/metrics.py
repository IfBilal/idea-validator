from prometheus_client import CollectorRegistry, Counter


class PrometheusFetchMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._injections = Counter(
            "blocked_injections_total",
            "Instruction-like fragments removed from fetched text",
            ["source"],
            registry=registry,
        )
        self._outcomes = Counter(
            "fetch_requests_total",
            "Fetch requests by outcome",
            ["source", "outcome"],
            registry=registry,
        )

    def injection(self, source: str, count: int) -> None:
        self._injections.labels(source=source).inc(count)

    def outcome(self, source: str, outcome: str) -> None:
        self._outcomes.labels(source=source, outcome=outcome).inc()
