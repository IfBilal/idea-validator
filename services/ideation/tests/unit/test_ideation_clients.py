import httpx
import pytest
from ideation_fakes import FakeAnalyst, FakeKb, FakeLlm, eid, evidence, make_service_client

from common.errors import BudgetExceeded, UpstreamError
from svc_ideation.clients.kb import KnowledgeBaseClient
from svc_ideation.clients.llm import LlmClient


async def test_llm_client_posts_openai_style_body_to_m5() -> None:
    fake = FakeLlm({"ok": True})
    reply = await fake.client().complete(
        run_id="run_1",
        agent="ideator",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=900,
    )
    body = fake.requests[0]
    assert body["run_id"] == "run_1"
    assert body["agent"] == "ideator"
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 900
    assert body["response_format"] == "json"
    assert body["model"] == "hosted-default"
    assert reply.content == '{"ok": true}'
    assert reply.tokens_in == 100
    assert reply.cost_usd == 0.001


async def test_llm_client_sends_the_internal_token_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"content": "{}"})

    client = LlmClient(make_service_client(handler, token="secret-token"))
    await client.complete(run_id="r", agent="a", messages=[], temperature=0, max_tokens=1)
    assert seen["x-internal-token"] == "secret-token"


async def test_llm_reply_in_openai_choices_shape_is_read() -> None:
    payload = {"choices": [{"message": {"role": "assistant", "content": '{"a": 1}'}}]}
    client = LlmClient(make_service_client(lambda _r: httpx.Response(200, json=payload)))
    reply = await client.complete(run_id="r", agent="a", messages=[], temperature=0, max_tokens=1)
    assert reply.content == '{"a": 1}'


async def test_llm_reply_reads_usage_from_openai_style_usage_block() -> None:
    payload = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    client = LlmClient(make_service_client(lambda _r: httpx.Response(200, json=payload)))
    reply = await client.complete(run_id="r", agent="a", messages=[], temperature=0, max_tokens=1)
    assert (reply.tokens_in, reply.tokens_out) == (7, 3)


async def test_llm_budget_error_propagates_as_budget_exceeded() -> None:
    with pytest.raises(BudgetExceeded):
        await (
            FakeLlm(402)
            .client()
            .complete(run_id="r", agent="a", messages=[], temperature=0, max_tokens=1)
        )


async def test_llm_upstream_failure_propagates() -> None:
    with pytest.raises(UpstreamError):
        await (
            FakeLlm(502)
            .client()
            .complete(run_id="r", agent="a", messages=[], temperature=0, max_tokens=1)
        )


@pytest.mark.parametrize(
    "payload",
    [{}, {"content": None}, {"content": 5}, ["x"], {"choices": []}, {"choices": [{"message": 1}]}],
)
async def test_llm_reply_without_string_content_is_an_upstream_error(payload: object) -> None:
    client = LlmClient(make_service_client(lambda _r: httpx.Response(200, json=payload)))
    with pytest.raises(UpstreamError):
        await client.complete(run_id="r", agent="a", messages=[], temperature=0, max_tokens=1)


async def test_kb_search_returns_typed_evidence_and_sends_filters() -> None:
    kb = FakeKb(search=lambda _b: [evidence(1), evidence(2)])
    items = await kb.client().search("slow export", k=5, kinds=["product"], run_id="run_1")
    assert [i.evidence_id for i in items] == [eid(1), eid(2)]
    body = kb.searches[0]
    assert body["query"] == "slow export"
    assert body["k"] == 5
    assert body["filters"]["kinds"] == ["product"]
    assert body["run_id"] == "run_1"
    assert body["mode"] == "hybrid"
    assert body["rerank"] is True


async def test_kb_search_without_kind_filter_omits_it() -> None:
    kb = FakeKb()
    await kb.client().search("q", k=3, run_id="r")
    assert "kinds" not in kb.searches[0]["filters"]


async def test_kb_search_with_malformed_items_is_an_upstream_error() -> None:
    client = kb_with({"items": [{"evidence_id": "bad"}]})
    with pytest.raises(UpstreamError):
        await client.search("q", k=3, run_id="r")


async def test_kb_search_without_items_key_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await kb_with({"nope": []}).search("q", k=3, run_id="r")


async def test_kb_existing_evidence_splits_found_from_missing() -> None:
    kb = FakeKb(known=[evidence(1)])
    found = await kb.client().existing_evidence([eid(1), eid(9)])
    assert list(found) == [eid(1)]


async def test_kb_existing_evidence_with_no_ids_makes_no_call() -> None:
    kb = FakeKb()
    assert await kb.client().existing_evidence([]) == {}
    assert kb.batches == []


async def test_kb_existing_evidence_deduplicates_ids() -> None:
    kb = FakeKb(known=[evidence(1)])
    await kb.client().existing_evidence([eid(1), eid(1)])
    assert kb.batches == [[eid(1)]]


async def test_kb_get_evidence_returns_none_on_404() -> None:
    kb = FakeKb(known=[evidence(1)])
    client = kb.client()
    assert (await client.get_evidence(eid(1))) is not None
    assert (await client.get_evidence(eid(2))) is None


async def test_analyst_returns_typed_gaps() -> None:
    analyst = FakeAnalyst(
        {"edtech": [{"gap_id": "gap_1", "text": "no offline mode", "evidence_ids": [eid(1)]}]}
    )
    gaps = await analyst.client().get_gaps("edtech")
    assert gaps[0].gap_id == "gap_1"
    assert gaps[0].evidence_ids == [eid(1)]
    assert analyst.calls == ["edtech"]


async def test_analyst_malformed_payload_is_an_upstream_error() -> None:
    from svc_ideation.clients.analyst import AnalystClient

    client = AnalystClient(make_service_client(lambda _r: httpx.Response(200, json={"gaps": 3})))
    with pytest.raises(UpstreamError):
        await client.get_gaps("x")


def kb_with(payload: object) -> KnowledgeBaseClient:
    return KnowledgeBaseClient(make_service_client(lambda _r: httpx.Response(200, json=payload)))
