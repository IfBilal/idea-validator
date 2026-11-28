"""The committed Grafana dashboard has the six required panels, built on metrics that exist."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / "k8s" / "dashboards" / "idea-validator.json"
REQUIRED = {
    "Cost per verdict",
    "Latency per agent",
    "Cache hit rate",
    "Fallbacks",
    "Blocked injections",
    "Budget stops",
}
# emitted by agentops today; the other two panels name the service that must emit them
AGENTOPS_SERIES = (
    "trace_cost_usd_total",
    "verdicts_total",
    "agent_step_latency_seconds",
    "cache_requests_total",
    "scorer_fallback_total",
)


def test_the_dashboard_is_valid_json_with_the_six_required_panels() -> None:
    data = json.loads(DASHBOARD.read_text())
    titles = [p["title"] for p in data["panels"]]
    assert set(titles) == REQUIRED and len(titles) == len(set(titles))
    assert data["uid"] == "idea-validator"
    assert len({p["id"] for p in data["panels"]}) == 6


def test_every_panel_has_a_query_and_a_data_source_variable() -> None:
    for panel in json.loads(DASHBOARD.read_text())["panels"]:
        assert panel["targets"] and all(t["expr"].strip() for t in panel["targets"])
        assert panel["datasource"]["uid"] == "${DS_PROMETHEUS}"


def test_panels_use_the_metric_names_agentops_registers() -> None:
    text = DASHBOARD.read_text()
    source = (ROOT / "services/agentops/src/svc_agentops/services/metrics.py").read_text()
    for name in AGENTOPS_SERIES:
        assert name in text and name in source


def test_panels_without_a_source_say_which_service_must_emit_them() -> None:
    panels = {p["title"]: p for p in json.loads(DASHBOARD.read_text())["panels"]}
    assert "tools (M5)" in panels["Blocked injections"]["description"]
    assert "orchestrator (M3)" in panels["Budget stops"]["description"]
