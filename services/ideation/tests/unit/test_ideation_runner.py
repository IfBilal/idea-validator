from typing import Any

import pytest
from ideation_fakes import PROMPTS_DIR, FakeLlm

from common.errors import BudgetExceeded
from common.schemas import TraceRecord
from common.tracing import Tracer
from svc_ideation.services.runner import AgentRunner

VALUES = {"idea_text": "an idea", "change_text": "a change"}


def parse_ok(data: dict[str, Any]) -> str | None:
    value = data.get("new_text")
    return value if isinstance(value, str) else None


def runner(fake: FakeLlm, records: list[TraceRecord] | None = None) -> AgentRunner:
    tracer = Tracer("ideation", sink=records.append if records is not None else None)
    return AgentRunner(fake.client(), PROMPTS_DIR, max_tokens=500, tracer=tracer)


async def test_valid_first_reply_is_parsed_with_prompt_temperature_and_agent_name():
    fake = FakeLlm({"new_text": "hello"})
    result = await runner(fake).run("idea_rewriter", "run_1", VALUES, parse_ok)
    assert result == "hello"
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request["agent"] == "idea_rewriter"
    assert request["temperature"] == 0.0
    assert request["max_tokens"] == 500
    assert request["messages"][0]["role"] == "system"


async def test_unparseable_reply_is_retried_once_with_a_json_only_reminder():
    fake = FakeLlm("I think the idea is great!", {"new_text": "fixed"})
    result = await runner(fake).run("idea_rewriter", "run_1", VALUES, parse_ok)
    assert result == "fixed"
    assert len(fake.requests) == 2
    assert fake.requests[1]["messages"][-1]["role"] == "user"
    assert "JSON" in fake.requests[1]["messages"][-1]["content"]


async def test_reply_failing_the_parser_is_retried_then_gives_up_with_none():
    fake = FakeLlm({"unrelated": 1})
    result = await runner(fake).run("idea_rewriter", "run_1", VALUES, parse_ok)
    assert result is None
    assert len(fake.requests) == 2  # exactly one retry, never more


async def test_upstream_errors_are_not_swallowed_or_retried():
    fake = FakeLlm(402)
    with pytest.raises(BudgetExceeded):
        await runner(fake).run("idea_rewriter", "run_1", VALUES, parse_ok)
    assert len(fake.requests) == 1


async def test_every_model_call_writes_a_trace_record_with_prompt_version():
    records: list[TraceRecord] = []
    fake = FakeLlm("garbage", {"new_text": "ok"})
    await runner(fake, records).run("idea_rewriter", "run_7", VALUES, parse_ok)
    assert [r.kind for r in records] == ["llm_call", "llm_call"]
    assert {r.run_id for r in records} == {"run_7"}
    assert records[0].agent == "idea_rewriter"
    assert records[0].prompt_version == "idea_rewriter@v1"
    assert records[0].tokens_in == 100
    assert records[0].cost_usd == 0.001


async def test_prompt_is_loaded_once_and_cached():
    fake = FakeLlm({"new_text": "a"})
    r = runner(fake)
    assert r.prompt("idea_rewriter") is r.prompt("idea_rewriter")
