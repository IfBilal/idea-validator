import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from common.errors import BudgetExceeded, Forbidden, RateLimited, UpstreamError, UpstreamTimeout
from common.schemas import FetchRequest, FetchResponse, SanitiserReport
from svc_tools.config import Settings
from svc_tools.main import build_app

HEADERS = {"X-Internal-Token": "tok"}
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[4] / "schemas" / "fetch.schema.json").read_text()
)
URL = "https://api.github.com/search?q=saas"


class FakeFetchService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.seen: list[FetchRequest] = []

    async def fetch(self, req: FetchRequest) -> FetchResponse:
        self.seen.append(req)
        if self.error:
            raise self.error
        return FetchResponse(
            url=req.url,
            final_url=req.url,
            status=200,
            content_type="application/json",
            text=None,
            json_={"items": [1, 2]},
            cache_hit=False,
            sanitiser=SanitiserReport(removed_patterns=0, flagged=False),
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


@pytest.fixture
def fake() -> FakeFetchService:
    return FakeFetchService()


@pytest.fixture
def client(fake: FakeFetchService) -> Iterator[TestClient]:
    app = build_app(Settings(internal_token="tok"), service=fake)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        yield test_client


def body(**changes: Any) -> dict[str, Any]:
    return {"url": URL, "source": "github", "purpose": "scout", **changes}


def test_a_valid_request_returns_a_response_that_matches_the_published_schema(
    client: TestClient,
) -> None:
    r = client.post("/fetch", json=body(run_id="run_1"), headers=HEADERS)
    assert r.status_code == 200
    validator = Draft202012Validator({**SCHEMA["$defs"]["response"], "$defs": SCHEMA["$defs"]})
    assert list(validator.iter_errors(r.json())) == []
    assert r.json()["json"] == {"items": [1, 2]}


def test_the_internal_token_is_required(client: TestClient) -> None:
    assert client.post("/fetch", json=body()).status_code == 401
    assert client.post("/fetch", json=body(), headers={"X-Internal-Token": "x"}).status_code == 401


@pytest.mark.parametrize(
    "bad",
    [
        {"url": "http://api.github.com/x"},
        {"url": "ftp://api.github.com/x"},
        {"url": ""},
        {"purpose": "crawl"},
        {"source": None},
        {"max_bytes": 5_000_001},
        {"max_bytes": "lots"},
        {"unexpected": True},
    ],
)
def test_invalid_requests_are_422(client: TestClient, bad: dict[str, Any]) -> None:
    assert client.post("/fetch", json=body(**bad), headers=HEADERS).status_code == 422


@pytest.mark.parametrize("size", [1, 2_000_000, 5_000_000])
def test_max_bytes_boundaries_are_accepted(client: TestClient, size: int) -> None:
    assert client.post("/fetch", json=body(max_bytes=size), headers=HEADERS).status_code == 200


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (Forbidden("no", {"reason": "DOMAIN_NOT_ALLOWED"}), 403, "FORBIDDEN"),
        (Forbidden("no", {"reason": "ROBOTS_DISALLOWED"}), 403, "FORBIDDEN"),
        (RateLimited("slow", {"retry_after": 7}), 429, "RATE_LIMITED"),
        (BudgetExceeded("over"), 402, "BUDGET_EXCEEDED"),
        (UpstreamTimeout("slow"), 504, "UPSTREAM_TIMEOUT"),
        (UpstreamError("down"), 502, "UPSTREAM_ERROR"),
    ],
)
def test_errors_map_to_the_documented_statuses(
    client: TestClient, fake: FakeFetchService, error: Exception, status: int, code: str
) -> None:
    fake.error = error
    r = client.post("/fetch", json=body(), headers=HEADERS)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_the_refusal_reason_is_visible_to_the_caller(
    client: TestClient, fake: FakeFetchService
) -> None:
    fake.error = Forbidden("host is not on the allow-list", {"reason": "DOMAIN_NOT_ALLOWED"})
    r = client.post("/fetch", json=body(), headers=HEADERS)
    assert r.json()["error"]["details"]["reason"] == "DOMAIN_NOT_ALLOWED"


def test_an_unexpected_failure_is_a_generic_500(client: TestClient, fake: FakeFetchService) -> None:
    fake.error = RuntimeError("redis password is hunter2")
    app = client.app
    with TestClient(app, raise_server_exceptions=False) as quiet:
        r = quiet.post("/fetch", json=body(), headers=HEADERS)
    assert r.status_code == 500
    assert "hunter2" not in r.text


def test_probes_and_metrics_are_exposed_and_include_the_injection_counter(
    client: TestClient,
) -> None:
    assert client.get("/health").json()["module"] == "M5"
    assert client.get("/ready").status_code == 200
    metrics = client.get("/metrics").text
    assert "http_requests_total" in metrics


def test_not_ready_and_refuses_to_start_without_redis_configuration() -> None:
    from common.config import ConfigError

    with pytest.raises(ConfigError, match="REDIS_URL"), TestClient(build_app(Settings())):
        pass
