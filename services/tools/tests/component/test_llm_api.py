"""POST /llm/v1/chat/completions over HTTP: auth, validation, error mapping, response shape."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from common.errors import BudgetExceeded, Forbidden, UpstreamError, UpstreamTimeout
from svc_tools.config import Settings
from svc_tools.main import build_app
from svc_tools.services.llm import LlmRequest, LlmResponse

HEADERS = {"X-Internal-Token": "tok"}
PATH = "/llm/v1/chat/completions"


class FakeLlmService:
    def __init__(self) -> None:
        self.seen: list[LlmRequest] = []
        self.error: Exception | None = None

    async def complete(self, req: LlmRequest) -> LlmResponse:
        self.seen.append(req)
        if self.error:
            raise self.error
        return LlmResponse(
            content='{"score": 4}',
            tokens_in=12,
            tokens_out=7,
            cost_usd=0.0004,
            latency_ms=31,
            provider="prov",
            model="prov/small",
        )


@pytest.fixture
def fake() -> FakeLlmService:
    return FakeLlmService()


@pytest.fixture
def client(fake: FakeLlmService) -> Iterator[TestClient]:
    app = build_app(Settings(internal_token="tok"), service=object(), llm=fake)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        yield test_client


def body(**changes: Any) -> dict[str, Any]:
    base = {
        "run_id": "run_90",
        "agent": "scorer",
        "model": "hosted-default",
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        "max_tokens": 800,
        "temperature": 0,
        "response_format": "json",
    }
    return {**base, **changes}


def test_the_documented_request_returns_the_documented_response(client: TestClient) -> None:
    r = client.post(PATH, json=body(), headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {
        "content": '{"score": 4}',
        "tokens_in": 12,
        "tokens_out": 7,
        "cost_usd": 0.0004,
        "latency_ms": 31,
        "provider": "prov",
        "model": "prov/small",
    }


def test_the_internal_token_is_required(client: TestClient, fake: FakeLlmService) -> None:
    assert client.post(PATH, json=body()).status_code == 401
    assert client.post(PATH, json=body(), headers={"X-Internal-Token": "x"}).status_code == 401
    assert fake.seen == []


def test_defaults_are_temperature_zero_and_text(client: TestClient, fake: FakeLlmService) -> None:
    minimal = {
        "run_id": "run_1",
        "agent": "critic",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 10,
    }
    assert client.post(PATH, json=minimal, headers=HEADERS).status_code == 200
    seen = fake.seen[0]
    assert (
        seen.temperature == 0 and seen.response_format == "text" and seen.model == "hosted-default"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"messages": []},
        {"messages": [{"role": "user", "content": "x"}] * 21},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "user", "content": "x" * 32_001}]},
        {"messages": [{"role": "user"}]},
        {"max_tokens": 0},
        {"max_tokens": 4097},
        {"max_tokens": "many"},
        {"temperature": -0.1},
        {"temperature": 1.5},
        {"response_format": "xml"},
        {"run_id": ""},
        {"run_id": "run 1; drop"},
        {"agent": "Scorer!"},
        {"unexpected": True},
        {"stream": True},
    ],
)
def test_invalid_requests_are_422_and_never_reach_the_service(
    client: TestClient, fake: FakeLlmService, bad: dict[str, Any]
) -> None:
    assert client.post(PATH, json=body(**bad), headers=HEADERS).status_code == 422
    assert fake.seen == []


@pytest.mark.parametrize("tokens", [1, 4096])
def test_max_tokens_boundaries_are_accepted(client: TestClient, tokens: int) -> None:
    assert client.post(PATH, json=body(max_tokens=tokens), headers=HEADERS).status_code == 200


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (BudgetExceeded("over", {"max_cost_usd": 0.05}), 402, "BUDGET_EXCEEDED"),
        (Forbidden("no", {"reason": "PROVIDER_NOT_ALLOWED"}), 403, "FORBIDDEN"),
        (UpstreamTimeout("slow"), 504, "UPSTREAM_TIMEOUT"),
        (UpstreamError("down"), 502, "UPSTREAM_ERROR"),
    ],
)
def test_errors_map_to_the_documented_statuses(
    client: TestClient, fake: FakeLlmService, error: Exception, status: int, code: str
) -> None:
    fake.error = error
    r = client.post(PATH, json=body(), headers=HEADERS)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code
