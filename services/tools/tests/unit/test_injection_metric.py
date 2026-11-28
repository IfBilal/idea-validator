"""blocked_injections_total{source}: what the dashboard panel "Blocked injections" reads."""

from prometheus_client import CollectorRegistry, generate_latest

from svc_tools.services.metrics import PrometheusFetchMetrics


def scrape(registry: CollectorRegistry) -> str:
    return generate_latest(registry).decode()


def test_removed_patterns_are_counted_per_source() -> None:
    registry = CollectorRegistry()
    metrics = PrometheusFetchMetrics(registry)
    metrics.injection("github", 2)
    metrics.injection("github", 1)
    metrics.injection("reddit", 4)
    text = scrape(registry)
    assert 'blocked_injections_total{source="github"} 3.0' in text
    assert 'blocked_injections_total{source="reddit"} 4.0' in text


def test_no_injection_means_no_series() -> None:
    registry = CollectorRegistry()
    PrometheusFetchMetrics(registry)
    assert "blocked_injections_total{" not in scrape(registry)
