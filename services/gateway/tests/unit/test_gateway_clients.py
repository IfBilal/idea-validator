"""Typed clients for M2 (ideas, runs) and M3 over ``httpx.MockTransport``."""

import uuid
from typing import Any

import httpx
import pytest

from common.errors import (
    BudgetExceeded,
    Conflict,
    DomainError,
    NotFound,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from common.http import ServiceClient
from svc_gateway.clients.events import RedisEventBus
from svc_gateway.clients.ideas import IdeaClient
from svc_gateway.clients.orchestrator import OrchestratorClient
from svc_gateway.testing import INTERNAL_TOKEN, FakeM2, FakeM3, clients, make_event

USER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
def m2() -> FakeM2:
    return FakeM2()


@pytest.fixture
def m3() -> FakeM3:
    return FakeM3()


@pytest.fixture
def pair(m2: FakeM2, m3: FakeM3) -> tuple[IdeaClient, OrchestratorClient]:
    ideas, orchestrator, _ = clients(m2, m3)
    return ideas, orchestrator


def raw_client(status: int, body: Any = None) -> ServiceClient:
    return ServiceClient(
        "http://secret-host.internal:8000",
        timeout_s=1,
        backoff_s=0,
        internal_token=INTERNAL_TOKEN,
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)),
    )


# ---- IdeaClient
async def test_create_idea_sends_the_user_and_returns_ids(pair: Any, m2: FakeM2) -> None:
    ideas, _ = pair
    created = await ideas.create_idea(USER, "Title", "Text")
    assert created.version == 1
    method, path, body = m2.calls[-1]
    assert (method, path) == ("POST", "/internal/ideas")
    assert body == {"user_id": USER, "title": "Title", "text": "Text", "source": "own"}
    assert m2.ideas[created.idea_id]["user_id"] == USER


async def test_add_version_is_owner_scoped(pair: Any, m2: FakeM2) -> None:
    ideas, _ = pair
    idea_id = m2.add_idea(USER)
    assert (await ideas.add_version(idea_id, USER, "v2")).version == 2
    with pytest.raises(NotFound):
        await ideas.add_version(idea_id, OTHER, "stolen")
    assert len(m2.versions[idea_id]) == 2


async def test_page_passes_user_limit_and_cursor_and_drops_unknown_fields(
    pair: Any, m2: FakeM2
) -> None:
    ideas, _ = pair
    for _ in range(3):
        m2.add_idea(USER)
    m2.add_idea(OTHER)
    page = await ideas.page(USER, 2, None)
    assert len(page.items) == 2
    assert page.next_cursor == "2"
    assert "user_id" not in page.items[0].model_dump()
    assert m2.calls[-1][2] == {"user_id": USER, "limit": "2"}
    second = await ideas.page(USER, 2, page.next_cursor)
    assert (len(second.items), second.next_cursor) == (1, None)
    assert m2.calls[-1][2]["cursor"] == "2"


async def test_versions_and_detail_are_owner_scoped(pair: Any, m2: FakeM2) -> None:
    ideas, _ = pair
    idea_id = m2.add_idea(USER, card={"verdict": "GO"}, verdict="GO", score=80)
    assert [v.version for v in await ideas.versions(idea_id, USER)] == [1]
    card = await ideas.detail(idea_id, USER, 1)
    assert card.card == {"verdict": "GO"}
    assert "internal_only" not in card.model_dump()
    for call in (
        ideas.versions(idea_id, OTHER),
        ideas.detail(idea_id, OTHER, 1),
        ideas.detail(idea_id, USER, 9),
    ):
        with pytest.raises(NotFound):
            await call


async def test_compare_sends_repeated_idea_ids(pair: Any, m2: FakeM2) -> None:
    ideas, _ = pair
    a, b = m2.add_idea(USER), m2.add_idea(USER)
    rows = await ideas.compare(USER, [a, b])
    assert [r.idea_id for r in rows] == [a, b]
    with pytest.raises(NotFound):
        await ideas.compare(OTHER, [a, b])


async def test_put_and_get_run_record(pair: Any, m2: FakeM2) -> None:
    ideas, _ = pair
    await ideas.put_run("run_1", USER, "validate", "running", {"idea_id": "x"})
    record = await ideas.get_run("run_1", USER)
    assert (record.run_id, record.kind, record.state) == ("run_1", "validate", "running")
    with pytest.raises(NotFound):
        await ideas.get_run("run_1", OTHER)
    with pytest.raises(NotFound):
        await ideas.put_run("run_1", OTHER, "validate", "running", {})


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (404, NotFound, "idea not found"),
        (409, Conflict, "conflict"),
        (422, ValidationFailed, "request failed validation"),
        (400, ValidationFailed, "request failed validation"),
        (
            401,
            UpstreamError,
            "temporarily unavailable",
        ),  # our own token was refused: not the user's fault
        (403, UpstreamError, "temporarily unavailable"),
        (429, UpstreamError, "temporarily unavailable"),
        (500, UpstreamError, "temporarily unavailable"),
        (502, UpstreamError, "temporarily unavailable"),
        (503, UpstreamError, "temporarily unavailable"),
        (402, BudgetExceeded, "budget"),
    ],
)
async def test_upstream_errors_become_public_safe_errors(
    status: int, error: type[DomainError], message: str
) -> None:
    ideas = IdeaClient(raw_client(status, {"error": {"message": "pg: password for secret-host"}}))
    with pytest.raises(error) as caught:
        await ideas.versions(str(uuid.uuid4()), USER)
    text = f"{caught.value.message} {caught.value.details}"
    assert message in text
    for leak in ("secret-host", "internal", "8000", "pg:", "password"):
        assert leak not in text


async def test_timeouts_become_a_public_timeout() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client = ServiceClient(
        "http://x.internal:8000",
        timeout_s=1,
        backoff_s=0,
        max_retries=0,
        transport=httpx.MockTransport(slow),
    )
    with pytest.raises(UpstreamTimeout) as caught:
        await IdeaClient(client).page(USER, 5, None)
    assert "internal" not in caught.value.message


async def test_malformed_upstream_bodies_are_a_502_not_a_500() -> None:
    for body in ({"unexpected": 1}, [1, 2], "text", None):
        with pytest.raises(UpstreamError):
            await IdeaClient(raw_client(200, body)).page(USER, 5, None)
    with pytest.raises(UpstreamError):
        await IdeaClient(raw_client(200, {"items": [{"idea_id": 1}], "next_cursor": None})).page(
            USER, 5, None
        )


# ---- OrchestratorClient
async def test_start_posts_kind_user_and_input(pair: Any, m3: FakeM3) -> None:
    _, orch = pair
    run_id = await orch.start("validate", USER, {"idea_id": "i", "version": 1, "idea_text": "t"})
    assert run_id.startswith("run_")
    assert m3.calls[-1][2] == {
        "kind": "validate",
        "user_id": USER,
        "input": {"idea_id": "i", "version": 1, "idea_text": "t"},
    }


async def test_get_returns_the_allow_listed_view(pair: Any, m3: FakeM3) -> None:
    _, orch = pair
    run_id = await orch.start("validate", USER, {})
    view = await orch.get(run_id)
    assert view is not None
    assert view.state == "running"
    assert "internal_only" not in view.model_dump()


async def test_get_of_a_run_m3_does_not_know_is_none(pair: Any) -> None:
    _, orch = pair
    assert await orch.get("run_nope") is None


async def test_answer_and_approve_forward_the_bodies(pair: Any, m3: FakeM3) -> None:
    _, orch = pair
    run_id = await orch.start("validate", USER, {})
    assert (await orch.answer(run_id, "q1", 120.0)) == "running"
    assert m3.calls[-1] == (
        "POST",
        f"/runs/{run_id}/answer",
        {"question_id": "q1", "answer": 120.0},
    )
    assert (await orch.approve(run_id, "a1", "decline")) == "running"
    assert m3.calls[-1][2] == {"approval_id": "a1", "decision": "decline"}


async def test_m3_conflict_is_public_and_generic(pair: Any, m3: FakeM3) -> None:
    _, orch = pair
    run_id = await orch.start("validate", USER, {})
    m3.conflict = True
    with pytest.raises(Conflict) as caught:
        await orch.answer(run_id, "q1", 1.0)
    assert "internal" not in caught.value.message


async def test_events_after_parses_and_validates(pair: Any, m3: FakeM3) -> None:
    _, orch = pair
    run_id = await orch.start("validate", USER, {})
    m3.events[run_id] = [make_event(run_id, n).model_dump(mode="json") for n in (1, 2, 3)]
    events = await orch.events_after(run_id, 1)
    assert [e.seq for e in events] == [2, 3]
    assert m3.calls[-1][2] == {"after": "1"}


async def test_events_after_degrades_to_nothing_when_m3_cannot_replay(
    pair: Any, m3: FakeM3
) -> None:
    _, orch = pair
    run_id = await orch.start("validate", USER, {})
    for status in (404, 500, 503):
        m3.replay_status = status
        assert await orch.events_after(run_id, 0) == []


async def test_events_after_drops_invalid_entries() -> None:
    good = make_event("run_1", 1).model_dump(mode="json")
    body = {"events": [good, {"seq": "x"}, "junk", {**good, "type": "nope", "seq": 2}]}
    orch = OrchestratorClient(raw_client(200, body))
    assert [e.seq for e in await orch.events_after("run_1", 0)] == [1]
    assert (
        await OrchestratorClient(raw_client(200, {"events": "no"})).events_after("run_1", 0) == []
    )
    assert await OrchestratorClient(raw_client(200, [1])).events_after("run_1", 0) == []


async def test_ready_reports_the_dependency(pair: Any, m3: FakeM3) -> None:
    _, orch = pair
    assert await orch.ready() is True
    m3.ready = False
    assert await orch.ready() is False


async def test_orchestrator_errors_are_public_safe() -> None:
    orch = OrchestratorClient(raw_client(500, {"error": "boom secret-host"}))
    with pytest.raises(UpstreamError) as caught:
        await orch.start("validate", USER, {})
    assert "secret-host" not in caught.value.message + str(caught.value.details)


# ---- RedisEventBus over a duck-typed pub/sub
class FakePubSub:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False
        self.waits: list[float] = []

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float = 0.0,  # noqa: ASYNC109 (mirrors redis-py)
    ) -> Any:
        self.waits.append(timeout)
        return self.messages.pop(0) if self.messages else None

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        return self._pubsub


async def test_redis_bus_subscribes_to_the_run_channel_and_decodes() -> None:
    pubsub = FakePubSub(
        [{"type": "message", "data": b'{"a": 1}'}, {"type": "message", "data": "text"}, None]
    )
    sub = await RedisEventBus(FakeRedis(pubsub)).subscribe("run_1")
    assert pubsub.subscribed == ["run:run_1"]
    assert await sub.next(2.5) == '{"a": 1}'
    assert await sub.next(2.5) == "text"
    assert await sub.next(2.5) is None
    assert pubsub.waits == [2.5, 2.5, 2.5]
    await sub.aclose()
    assert pubsub.unsubscribed == ["run:run_1"]
    assert pubsub.closed


async def test_redis_bus_ignores_non_message_frames_and_bad_bytes() -> None:
    pubsub = FakePubSub(
        [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"\xff\xfe"},
            {"type": "message", "data": 5},
        ]
    )
    sub = await RedisEventBus(FakeRedis(pubsub)).subscribe("run_1")
    assert [await sub.next(1), await sub.next(1), await sub.next(1)] == [None, None, None]


async def test_redis_bus_closes_even_if_unsubscribe_fails() -> None:
    pubsub = FakePubSub([])

    async def boom(channel: str) -> None:
        raise OSError("gone")

    pubsub.unsubscribe = boom  # type: ignore[method-assign]
    sub = await RedisEventBus(FakeRedis(pubsub)).subscribe("run_1")
    await sub.aclose()
    assert pubsub.closed


async def test_redis_bus_closes_the_pubsub_if_subscribing_fails() -> None:
    pubsub = FakePubSub([])

    async def boom(channel: str) -> None:
        raise OSError("refused")

    pubsub.subscribe = boom  # type: ignore[method-assign]
    with pytest.raises(OSError, match="refused"):
        await RedisEventBus(FakeRedis(pubsub)).subscribe("run_1")
    assert pubsub.closed


async def test_a_start_answer_without_a_run_id_is_a_502() -> None:
    bodies: list[Any] = [{}, {"run_id": ""}, {"run_id": 5}, [], "x"]
    for body in bodies:
        with pytest.raises(UpstreamError):
            await OrchestratorClient(raw_client(202, body)).start("validate", USER, {})


async def test_a_list_endpoint_that_answers_with_an_object_is_a_502() -> None:
    with pytest.raises(UpstreamError):
        await IdeaClient(raw_client(200, {"versions": []})).versions(str(uuid.uuid4()), USER)
