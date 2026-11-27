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


class PrometheusLlmMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._tokens = Counter(
            "llm_tokens_total", "LLM tokens", ["alias", "direction"], registry=registry
        )
        self._cost = Counter("llm_cost_usd_total", "LLM spend in USD", ["alias"], registry=registry)

    def usage(self, alias: str, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        self._tokens.labels(alias=alias, direction="in").inc(tokens_in)
        self._tokens.labels(alias=alias, direction="out").inc(tokens_out)
        self._cost.labels(alias=alias).inc(cost_usd)
