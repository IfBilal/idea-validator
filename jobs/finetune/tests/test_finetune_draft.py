import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from finetune.draft_labels import (
    ChatResult,
    CostTracker,
    HostedClient,
    draft_candidate,
    load_prices,
    run_drafting,
)
from finetune.prompting import PromptFactory, repo_root
from finetune.schema import validate_example

EV = [{"evidence_id": "card_8f21aa01#c2", "quote": "Users hate slow exports"}]


def cand(idea_id: str = "i1", evidence: Any = EV, dim: str = "demand_pain") -> dict[str, Any]:
    return {
        "idea_id": idea_id,
        "idea_text": "A faster export tool",
        "dimension": dim,
        "evidence": evidence,
        "features": {"n_sources": 1},
    }


def factory() -> PromptFactory:
    return PromptFactory(repo_root() / "rubrics", repo_root() / "prompts")


class Fake:
    model = "fake"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, int]] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> ChatResult:
        self.calls.append((system, user, max_tokens))
        return ChatResult(text=self.replies.pop(0), tokens_in=100, tokens_out=50)


GOOD = json.dumps(
    {"score": 3.5, "confidence": 0.7, "evidence_ids": ["card_8f21aa01#c2"], "reason": "Cited."}
)


def tracker() -> CostTracker:
    return CostTracker(price_in_per_mtok=0.15, price_out_per_mtok=0.6)


def test_good_reply_becomes_a_valid_unchecked_example() -> None:
    client = Fake(GOOD)
    ex = draft_candidate(factory(), client, cand(), tracker(), max_tokens=800)
    assert ex is not None
    v = validate_example(ex)
    assert v.meta.hand_checked is False and v.meta.level == 3.5
    assert v.meta.draft_model == "fake"
    assert client.calls[0][2] == 800  # reasoning models need a generous budget


def test_no_evidence_abstains_without_calling_the_model() -> None:
    client = Fake()
    ex = draft_candidate(factory(), client, cand(evidence=[]), tracker(), max_tokens=800)
    assert ex is not None and ex["meta"]["level"] is None
    assert client.calls == []


def test_invalid_reply_is_retried_once_then_dropped() -> None:
    client = Fake("nope", "still nope")
    assert draft_candidate(factory(), client, cand(), tracker(), max_tokens=800) is None
    assert len(client.calls) == 2


def test_retry_recovers() -> None:
    client = Fake("nope", GOOD)
    assert draft_candidate(factory(), client, cand(), tracker(), max_tokens=800) is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"score": 3.25, "confidence": 0.5, "evidence_ids": ["card_8f21aa01#c2"], "reason": "r"},
        {"score": 3, "confidence": 0.5, "evidence_ids": ["card_dead0000#c9"], "reason": "r"},
        {"score": 3, "confidence": 0.5, "evidence_ids": ["card_8f21aa01#c2"], "reason": "GO now"},
        {
            "score": 3,
            "confidence": 0.5,
            "evidence_ids": ["card_8f21aa01#c2"],
            "reason": "r",
            "x": 1,
        },
    ],
)
def test_contract_violations_are_never_accepted(bad: dict[str, Any]) -> None:
    client = Fake(json.dumps(bad), json.dumps(bad))
    assert draft_candidate(factory(), client, cand(), tracker(), max_tokens=800) is None


def test_cost_tracker_accumulates() -> None:
    t = tracker()
    t.add(1_000_000, 0)
    t.add(0, 1_000_000)
    assert t.cost_usd == pytest.approx(0.75)
    assert t.calls == 2


def test_run_drafting_writes_jsonl_and_summary(tmp_path: Path) -> None:
    out = tmp_path / "drafts.jsonl"
    client = Fake(GOOD, "bad", "bad")
    summary = run_drafting(
        factory(),
        client,
        [cand("a"), cand("b")],
        out_path=out,
        tracker=tracker(),
        max_tokens=800,
    )
    assert summary["written"] == 1 and summary["dropped"] == 1
    assert len(out.read_text().splitlines()) == 1


def test_run_drafting_resumes_without_repeating_work(tmp_path: Path) -> None:
    out = tmp_path / "drafts.jsonl"
    run_drafting(
        factory(), Fake(GOOD), [cand("a")], out_path=out, tracker=tracker(), max_tokens=800
    )
    c = Fake(GOOD)
    run_drafting(
        factory(), c, [cand("a"), cand("b")], out_path=out, tracker=tracker(), max_tokens=800
    )
    assert len(c.calls) == 1
    assert len(out.read_text().splitlines()) == 2


def test_load_prices_reads_the_shared_llm_config() -> None:
    p = load_prices(repo_root() / "config" / "llm.yaml", "hosted-default")
    assert p["model"] == "openai/gpt-oss-120b"
    assert p["price_in_per_mtok"] > 0


@respx.mock
def test_hosted_client_posts_openai_format_and_retries_429() -> None:
    route = respx.post("https://api.example/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={}),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": GOOD}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            ),
        ]
    )
    client = HostedClient(
        base_url="https://api.example/v1", api_key="k", model="m", sleep=lambda _s: None
    )
    res = client.complete(system="s", user="u", max_tokens=800)
    assert res.text == GOOD and res.tokens_in == 10 and res.tokens_out == 5
    assert route.call_count == 2
    body = json.loads(route.calls[0].request.content)
    assert body["temperature"] == 0 and body["max_tokens"] == 800
    assert body["response_format"] == {"type": "json_object"}
    assert route.calls[0].request.headers["authorization"] == "Bearer k"
