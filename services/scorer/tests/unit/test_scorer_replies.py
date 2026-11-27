"""Parsing and validating the model's reply (equivalence classes + boundaries)."""

import json
from typing import Any

import pytest

from svc_scorer.services.replies import ReplyError, parse_reply, resolve_evidence

IDS = {"card_8f21a3b4#c2", "card_77a1b2c3#c0"}


def _reply(**over: Any) -> str:
    body: dict[str, Any] = {
        "score": 3.5,
        "confidence": 0.7,
        "evidence_ids": ["card_8f21a3b4#c2"],
        "reason": "Two sources show the problem.",
    }
    body.update(over)
    return json.dumps(body)


def test_valid_reply_parses() -> None:
    parsed = parse_reply(_reply())
    assert (parsed.score, parsed.confidence) == (3.5, 0.7)
    assert parsed.evidence_ids == ["card_8f21a3b4#c2"]


def test_code_fence_is_tolerated() -> None:
    assert parse_reply("```json\n" + _reply() + "\n```").score == 3.5


@pytest.mark.parametrize("score", [0, 0.0, 5, 5.0, 2.5])
def test_score_boundaries_accepted(score: float) -> None:
    assert parse_reply(_reply(score=score)).score == score


@pytest.mark.parametrize("score", [-0.01, 5.01, 6, -1, 1e9])
def test_score_out_of_range_rejected(score: float) -> None:
    with pytest.raises(ReplyError):
        parse_reply(_reply(score=score))


@pytest.mark.parametrize("confidence", [0, 1, 0.5])
def test_confidence_boundaries_accepted(confidence: float) -> None:
    assert parse_reply(_reply(confidence=confidence)).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 7])
def test_confidence_out_of_range_rejected(confidence: float) -> None:
    with pytest.raises(ReplyError):
        parse_reply(_reply(confidence=confidence))


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", "3.5"),
        ("score", True),
        ("score", [3]),
        ("confidence", "high"),
        ("confidence", True),
        ("confidence", None),
        ("evidence_ids", "card_8f21a3b4#c2"),
        ("evidence_ids", [1, 2]),
        ("evidence_ids", None),
        ("reason", 5),
        ("reason", None),
    ],
)
def test_wrong_types_rejected(field: str, value: object) -> None:
    with pytest.raises(ReplyError):
        parse_reply(_reply(**{field: value}))


@pytest.mark.parametrize("missing", ["score", "confidence", "evidence_ids", "reason"])
def test_missing_field_rejected(missing: str) -> None:
    body = json.loads(_reply())
    del body[missing]
    with pytest.raises(ReplyError):
        parse_reply(json.dumps(body))


@pytest.mark.parametrize(
    "text",
    ["", "   ", "not json", "{", "[1, 2]", '"a string"', "null", "NaN", '{"score": NaN}'],
)
def test_malformed_json_rejected(text: str) -> None:
    with pytest.raises(ReplyError):
        parse_reply(text)


def test_null_score_is_an_abstention_with_zero_confidence() -> None:
    parsed = parse_reply(_reply(score=None, confidence=0.9, evidence_ids=[]))
    assert parsed.score is None
    assert parsed.confidence == 0


def test_reason_is_truncated_to_the_schema_limit() -> None:
    assert len(parse_reply(_reply(reason="x" * 5000)).reason) == 600


def test_verdict_like_keys_are_dropped() -> None:
    parsed = parse_reply(_reply(verdict="GO", recommendation="build it"))
    assert not hasattr(parsed, "verdict")


def test_duplicate_ids_are_deduplicated_in_order() -> None:
    parsed = parse_reply(
        _reply(evidence_ids=["card_8f21a3b4#c2", "card_77a1b2c3#c0", "card_8f21a3b4#c2"])
    )
    assert parsed.evidence_ids == ["card_8f21a3b4#c2", "card_77a1b2c3#c0"]


def test_resolve_evidence_keeps_only_provided_ids() -> None:
    kept = resolve_evidence(["card_8f21a3b4#c2", "card_deadbeef#c9"], IDS)
    assert kept == ["card_8f21a3b4#c2"]


def test_resolve_evidence_with_nothing_valid_is_empty() -> None:
    assert resolve_evidence(["card_deadbeef#c9"], IDS) == []
    assert resolve_evidence([], IDS) == []
