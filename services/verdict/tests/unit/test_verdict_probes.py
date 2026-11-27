from conftest import CONFIG_FILE
from fastapi.testclient import TestClient

from svc_verdict.config import Settings
from svc_verdict.main import build_app


def _app() -> TestClient:
    return TestClient(build_app(Settings(config_dir=CONFIG_FILE.parent, internal_token="tok")))


def test_health_identifies_the_service() -> None:
    with _app() as client:
        body = client.get("/health").json()
    assert (body["service"], body["module"]) == ("verdict", "M11")


def test_ready_only_after_the_rules_are_loaded() -> None:
    app = build_app(Settings(config_dir=CONFIG_FILE.parent))
    before = TestClient(app).get("/ready")  # no lifespan yet: rules not loaded
    assert before.status_code == 503
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200


def test_metrics_are_exposed() -> None:
    with _app() as client:
        assert "http_requests_total" in client.get("/metrics").text
