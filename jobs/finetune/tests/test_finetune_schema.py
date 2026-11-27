import json
from typing import Any

import pytest

from finetune.schema import ExampleError, validate_example

EV = "card_8f21aa01#c2"
EV2 = "card_77a1bb02#c0"


def make(
    score: Any = 3.5,
    conf: Any = 0.7,
    ids: Any = (EV,),
    evidence: Any = (EV, EV2),
    dim: str = "demand_pain",
    **meta: Any,
) -> Any:
    user = "IDEA: x\nFEATURES: {}\nEVIDENCE:\n" + "\n".join(
        f'<evidence id="{e}">q</evidence>' for e in evidence
    )
    reply = {"score": score, "confidence": conf, "evidence_ids": list(ids), "reason": "ok"}
    m = {
        "idea_id": "i1",
        "dimension": dim,
        "level": score,
        "hand_checked": False,
        "rubric_version": f"{dim}@v1",
        "evidence_ids": list(evidence),
    }
    m.update(meta)
    return {
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(reply)},
        ],
        "meta": m,
    }


def test_valid_example_passes() -> None:
    assert validate_example(make()).meta.dimension == "demand_pain"


@pytest.mark.parametrize("score", [0, 0.5, 2.5, 5])
def test_score_grid_accepted(score: Any) -> None:
    validate_example(make(score=score))


@pytest.mark.parametrize("score", [-0.5, 5.5, 3.2, 3.01, float("nan")])
def test_score_off_grid_rejected(score: Any) -> None:
    ex = make()
    ex["messages"][2]["content"] = json.dumps(
        {"score": score, "confidence": 0.5, "evidence_ids": [EV], "reason": "r"}
    ).replace("NaN", "NaN")
    with pytest.raises(ExampleError):
        validate_example(ex)


def test_invented_evidence_id_rejected() -> None:
    with pytest.raises(ExampleError, match="not in the evidence"):
        validate_example(make(ids=("card_deadbeef#c9",)))


def test_scored_example_needs_at_least_one_evidence_id() -> None:
    with pytest.raises(ExampleError, match="evidence"):
        validate_example(make(ids=()))


def test_abstain_rule() -> None:
    ok = make(score=None, conf=0, ids=(), evidence=())
    ok["meta"]["level"] = None
    validate_example(ok)
    bad = make(score=None, conf=0.4, ids=(), evidence=())
    bad["meta"]["level"] = None
    with pytest.raises(ExampleError, match="abstain"):
        validate_example(bad)
    bad2 = make(score=None, conf=0, ids=(EV,))
    bad2["meta"]["level"] = None
    with pytest.raises(ExampleError, match="abstain"):
        validate_example(bad2)


def test_no_evidence_in_prompt_must_abstain() -> None:
    with pytest.raises(ExampleError):
        validate_example(make(evidence=(), ids=()))


def test_verdict_words_forbidden_in_target() -> None:
    for word in ("GO", "NO-GO", "verdict", "PIVOT"):
        ex = make()
        ex["messages"][2]["content"] = json.dumps(
            {"score": 3, "confidence": 0.5, "evidence_ids": [EV], "reason": f"final {word} here"}
        )
        with pytest.raises(ExampleError, match="verdict"):
            validate_example(ex)


def test_extra_keys_in_target_rejected() -> None:
    ex = make()
    ex["messages"][2]["content"] = json.dumps(
        {"score": 3, "confidence": 0.5, "evidence_ids": [EV], "reason": "r", "verdict": "GO"}
    )
    with pytest.raises(ExampleError):
        validate_example(ex)


def test_roles_and_shape() -> None:
    ex = make()
    ex["messages"][1]["role"] = "assistant"
    with pytest.raises(ExampleError):
        validate_example(ex)
    ex = make()
    del ex["meta"]["idea_id"]
    with pytest.raises(ExampleError):
        validate_example(ex)


def test_hand_checked_requires_audit_fields() -> None:
    ex = make(hand_checked=True)
    with pytest.raises(ExampleError, match="checked_by"):
        validate_example(ex)
    ex["meta"].update(
        checked_by="taimoor",
        checked_at="2026-11-27T10:00:00Z",
        original_label=3.0,
        final_label=3.5,
    )
    validate_example(ex)


def test_unknown_dimension_and_confidence_range() -> None:
    with pytest.raises(ExampleError):
        validate_example(make(dim="vibes"))
    with pytest.raises(ExampleError):
        validate_example(make(conf=1.5))


def test_synthetic_flag_allowed_but_surfaced() -> None:
    assert validate_example(make(synthetic=True)).meta.synthetic is True
