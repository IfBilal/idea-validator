import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from common.errors import BudgetExceeded, RateLimited, UpstreamError
from svc_tools.config import Settings
from svc_tools.main import build_app
from svc_tools.models.llm import LlmRequest, LlmResponse

H = {"X-Internal-Token": "tok"}
PATH = "/llm/v1/chat/completions"
BODY: dict[str, Any] = {
    "run_id": "run_1",
    "agent": "scorer",
    "model": "hosted-default",
    "messages": [{"role": "user", "content": "hi"}],
}


class FakeLlm:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.seen: list[LlmRequest] = []

    async def complete(self, req: LlmRequest) -> LlmResponse:
        self.seen.append(req)
        if self.error:
            raise self.error
        return LlmResponse(
            content='{"score": 3}', tokens_in=10, tokens_out=5, cost_usd=Decimal("0.000012"),
            latency_ms=42, provider="groq", model="llama",
        )  # fmt: skip


@pytest.fixture
def fake() -> FakeLlm:
    return FakeLlm()


@pytest.fixture
def client(fake: FakeLlm) -> Iterator[TestClient]:
    from test_fetch_api import FakeFetchService

    app = build_app(Settings(internal_token="tok"), service=FakeFetchService(), llm=fake)  # type: ignore[arg-type]
    with TestClient(app) as c:
        yield c


def test_the_response_has_both_the_documented_fields_and_an_openai_shape(
    client: TestClient,
) -> None:
    r = client.post(PATH, json=BODY, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == '{"score": 3}'
    assert (body["tokens_in"], body["tokens_out"], body["latency_ms"]) == (10, 5, 42)
    assert body["cost_usd"] == pytest.approx(0.000012)
    assert (body["provider"], body["model"]) == ("groq", "llama")
    assert body["choices"][0]["message"] == {"role": "assistant", "content": '{"score": 3}'}
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_the_internal_token_is_required(client: TestClient) -> None:
    assert client.post(PATH, json=BODY).status_code == 401
    assert client.post(PATH, json=BODY, headers={"X-Internal-Token": "x"}).status_code == 401


def test_invalid_bodies_are_422_and_never_reach_the_service(
    client: TestClient, fake: FakeLlm
) -> None:
    for bad in ({**BODY, "max_tokens": 0}, {**BODY, "stream": True}, {**BODY, "messages": []}, {}):
        assert client.post(PATH, json=bad, headers=H).status_code == 422
    assert fake.seen == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (BudgetExceeded("over"), 402, "BUDGET_EXCEEDED"),
        (RateLimited("slow", {"retry_after": 4}), 429, "RATE_LIMITED"),
        (UpstreamError("down"), 502, "UPSTREAM_ERROR"),
    ],
)
def test_errors_map_to_the_documented_statuses(
    client: TestClient, fake: FakeLlm, error: Exception, status: int, code: str
) -> None:
    fake.error = error
    r = client.post(PATH, json=BODY, headers=H)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)


def test_prompts_are_not_in_the_metrics_or_error_bodies(client: TestClient, fake: FakeLlm) -> None:
    fake.error = RuntimeError("boom")
    secret = {**BODY, "messages": [{"role": "user", "content": "very secret plan"}]}
    quiet = TestClient(client.app, raise_server_exceptions=False)
    r = quiet.post(PATH, json=secret, headers=H)
    assert r.status_code == 500 and "very secret plan" not in r.text
    assert "very secret plan" not in client.get("/metrics").text
    json.dumps(r.json())
