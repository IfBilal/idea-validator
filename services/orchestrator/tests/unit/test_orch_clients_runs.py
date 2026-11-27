"""ProfileClient.list_runs: the recovery listing of M2's run mirror."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from common.errors import UpstreamError
from common.http import ServiceClient
from svc_orchestrator.clients.profile import ProfileClient, StoredRun

ROW = {
    "run_id": "run_1",
    "user_id": "11111111-1111-1111-1111-111111111111",
    "kind": "validate",
    "state": "awaiting_input",
    "input": {"idea_text": "x"},
    "context": {"step": "WAIT_ANSWER"},
    "pending_question": {"question_id": "q1"},
    "created_at": "2026-05-01T00:00:00Z",
    "updated_at": "2026-05-01T00:00:00Z",
}


class Recorder:
    def __init__(self, response: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response(request)


def client(handler: Callable[[httpx.Request], httpx.Response]) -> ProfileClient:
    def make(h: Callable[[httpx.Request], httpx.Response]) -> ServiceClient:
        return ServiceClient(
            "http://svc",
            timeout_s=1,
            max_retries=0,
            internal_token="tok",
            backoff_s=0.0,
            transport=httpx.MockTransport(h),
        )

    return ProfileClient(make(handler), make(lambda r: httpx.Response(200, json={})))


def ok(body: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda r: httpx.Response(200, json=body)


async def test_list_runs_asks_for_the_states_a_page_and_a_cursor() -> None:
    rec = Recorder(ok([ROW]))
    runs = await client(rec).list_runs(["running", "awaiting_input"], limit=50, after="run_0")
    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/internal/runs"
    assert dict(request.url.params) == {
        "state": "running,awaiting_input",
        "limit": "50",
        "after": "run_0",
    }
    assert runs == [StoredRun.model_validate(ROW)]
    assert runs[0].context == {"step": "WAIT_ANSWER"}


async def test_list_runs_first_page_has_no_cursor() -> None:
    rec = Recorder(ok([]))
    assert await client(rec).list_runs(["running"]) == []
    assert "after" not in rec.requests[0].url.params


@pytest.mark.parametrize("body", [{"runs": []}, "no", [1], [{"run_id": "x"}], None])
async def test_a_malformed_listing_is_an_upstream_error_not_a_crash(body: object) -> None:
    with pytest.raises(UpstreamError):
        await client(Recorder(ok(body))).list_runs(["running"])


async def test_an_m2_failure_surfaces_as_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await client(lambda r: httpx.Response(500, json={})).list_runs(["running"])


def test_a_stored_run_keeps_only_what_recovery_needs_and_tolerates_new_fields() -> None:
    run = StoredRun.model_validate({**ROW, "brand_new": 1})
    assert (run.run_id, run.kind, run.state) == ("run_1", "validate", "awaiting_input")
    assert run.pending_question == {"question_id": "q1"}
