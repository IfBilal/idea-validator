from fastapi.testclient import TestClient

from svc_gateway.config import Settings
from svc_gateway.main import build_app


def test_health_identifies_the_service() -> None:
    body = TestClient(build_app(Settings())).get("/health").json()
    assert (body["service"], body["module"]) == ("gateway", "M1")


def test_not_ready_without_redis_and_the_profile_service() -> None:
    response = TestClient(build_app(Settings())).get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"redis": False, "profile": False}


def test_metrics_are_exposed() -> None:
    assert "http_requests_total" in TestClient(build_app(Settings())).get("/metrics").text
