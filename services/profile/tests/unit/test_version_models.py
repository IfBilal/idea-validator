import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from svc_profile.models.storage import PutVersion

U = str(uuid.uuid4())


def body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": U,
        "text": "t",
        "change_note": "n",
        "card": {"verdict": None},
        "idempotency_key": "k1",
        "base_version": 1,
    }
    return {**base, **over}


def test_valid_body_keeps_a_null_verdict_card_verbatim() -> None:
    assert PutVersion(**body()).card == {"verdict": None}


@pytest.mark.parametrize(
    "bad",
    [
        {"text": ""},
        {"text": "x" * 4001},
        {"change_note": "x" * 501},
        {"idempotency_key": ""},
        {"idempotency_key": "a b"},
        {"idempotency_key": "k" * 101},
        {"base_version": 0},
        {"user_id": "nope"},
        {"extra": 1},
        {"card": {"a": "x" * 262_145}},
        {"card": {"a": float("nan")}},
        {"card": [1]},
    ],
)
def test_invalid_bodies_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PutVersion(**body(**bad))


def test_card_just_under_the_cap_is_accepted() -> None:
    assert PutVersion(**body(card={"a": "x" * 262_000}))


def test_boundary_lengths_are_accepted() -> None:
    PutVersion(**body(text="x" * 4000, change_note="x" * 500, idempotency_key="k" * 100))
