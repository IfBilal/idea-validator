"""Local-first scoring (docs/harness/11 §4): GGUF first, retry once, hosted fallback, then abstain."""

import json
from pathlib import Path
from typing import Any

import pytest

from common.errors import UpstreamError, ValidationFailed
from common.schemas import ScoreEvidence, ScoreRequest
from svc_scorer.services.prompt import PromptBuilder
from svc_scorer.services.scoring import ScoringService

ROOT = Path(__file__).resolve().parents[4]
ID1, ID2 = "card_8f21a3b4#c2", "card_77a1b2c3#c0"
LOCAL, HOSTED = "scorer-lora@v1", "hosted-default"


class FakeLlm:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def complete(self, *, run_id: str, system: str, user: str, max_tokens: int) -> str:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _reply(**over: Any) -> str:
    body: dict[str, Any] = {
        "score": 3.5,
        "confidence": 0.7,
        "evidence_ids": [ID1],
        "reason": "Two sources show the problem.",
    }
    body.update(over)
    return json.dumps(body)


def _request() -> ScoreRequest:
    return ScoreRequest(
        rubric_id="demand_pain@v1",
        idea="A study planner",
        evidence=[
            ScoreEvidence(evidence_id=ID1, quote="notes export is slow"),
            ScoreEvidence(evidence_id=ID2, quote="no offline mode"),
        ],
        features={"n_sources": 2},
        run_id="run_1",
    )


def _service(
    local: FakeLlm, hosted: FakeLlm | None, fallbacks: list[str] | None = None
) -> ScoringService:
    return ScoringService(
        local,
        PromptBuilder(ROOT / "rubrics", ROOT / "prompts"),
        model_name=LOCAL,
        fallback_llm=hosted,
        fallback_model_name=HOSTED,
        on_fallback=(fallbacks.append if fallbacks is not None else None),
    )


async def test_valid_local_reply_is_used_and_tagged_with_the_local_model() -> None:
    local, hosted = FakeLlm(_reply()), FakeLlm()
    score = await _service(local, hosted).score(_request())
    assert score.model == LOCAL
    assert score.fallback is False and score.abstained is False
    assert (local.calls, hosted.calls) == (1, 0)


async def test_invalid_local_reply_is_retried_once_before_any_fallback() -> None:
    local, hosted = FakeLlm("not json", _reply()), FakeLlm()
    fallbacks: list[str] = []
    score = await _service(local, hosted, fallbacks).score(_request())
    assert score.model == LOCAL and score.fallback is False
    assert (local.calls, hosted.calls) == (2, 0)
    assert fallbacks == []


async def test_two_invalid_local_replies_fall_back_to_hosted_and_log_it() -> None:
    local, hosted = FakeLlm("bad", "worse"), FakeLlm(_reply(score=2.0))
    fallbacks: list[str] = []
    score = await _service(local, hosted, fallbacks).score(_request())
    assert score.model == HOSTED
    assert score.fallback is True and score.score == 2.0
    assert fallbacks == ["invalid_output"]
    assert (local.calls, hosted.calls) == (2, 1)


async def test_invented_evidence_ids_from_the_local_model_count_as_invalid() -> None:
    bad = _reply(evidence_ids=["card_deadbeef#c9"])
    local, hosted = FakeLlm(bad, bad), FakeLlm(_reply())
    fallbacks: list[str] = []
    score = await _service(local, hosted, fallbacks).score(_request())
    assert score.model == HOSTED and score.evidence_ids == [ID1]
    assert fallbacks == ["invalid_output"]


async def test_local_server_down_falls_back_without_burning_retries() -> None:
    local, hosted = FakeLlm(UpstreamError("down")), FakeLlm(_reply())
    fallbacks: list[str] = []
    score = await _service(local, hosted, fallbacks).score(_request())
    assert score.model == HOSTED and score.fallback is True
    assert fallbacks == ["unavailable"]
    assert local.calls == 1


async def test_both_models_invalid_abstain_with_the_fallback_flag() -> None:
    local, hosted = FakeLlm("x", "y"), FakeLlm("p", "q")
    score = await _service(local, hosted).score(_request())
    assert score.abstained is True and score.score is None and score.confidence == 0
    assert score.fallback is True
    assert (local.calls, hosted.calls) == (2, 2)


async def test_hosted_transport_error_propagates_after_a_local_failure() -> None:
    local, hosted = FakeLlm("x", "y"), FakeLlm(UpstreamError("m5 down"))
    with pytest.raises(UpstreamError):
        await _service(local, hosted).score(_request())


async def test_local_abstention_is_honoured_and_never_second_guessed() -> None:
    abstain = json.dumps(
        {"score": None, "confidence": 0, "evidence_ids": [], "reason": "no evidence"}
    )
    local, hosted = FakeLlm(abstain), FakeLlm()
    score = await _service(local, hosted).score(_request())
    assert score.abstained is True and score.fallback is False
    assert hosted.calls == 0


async def test_no_evidence_calls_neither_model() -> None:
    local, hosted = FakeLlm(), FakeLlm()
    request = _request().model_copy(update={"evidence": []})
    score = await _service(local, hosted).score(request)
    assert score.abstained is True
    assert (local.calls, hosted.calls) == (0, 0)


async def test_local_only_mode_has_no_hosted_fallback_and_abstains() -> None:
    local = FakeLlm("x", "y")
    score = await _service(local, None).score(_request())
    assert score.abstained is True and score.fallback is True


async def test_stray_verdict_key_from_the_local_model_never_survives() -> None:
    local = FakeLlm(_reply(verdict="GO"))
    score = await _service(local, None).score(_request())
    assert "verdict" not in score.model_dump()


async def test_unknown_rubric_is_rejected_before_any_call() -> None:
    local, hosted = FakeLlm(), FakeLlm()
    request = _request().model_copy(update={"rubric_id": "vibes@v1"})
    with pytest.raises(ValidationFailed):
        await _service(local, hosted).score(request)
    assert (local.calls, hosted.calls) == (0, 0)
