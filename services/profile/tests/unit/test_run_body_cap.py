"""RunBody: input and context are size-capped so a run row cannot be made arbitrarily large."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from svc_profile.models.storage import MAX_RUN_JSON_BYTES, RunBody


def body(**over: Any) -> dict[str, Any]:
    return {"user_id": uuid.uuid4(), "kind": "validate", "state": "running", "input": {}, **over}


def padded(total_bytes: int) -> dict[str, str]:
    overhead = len(json.dumps({"x": ""}))
    return {"x": "a" * (total_bytes - overhead)}


def test_a_json_cap_of_64_kb() -> None:
    assert MAX_RUN_JSON_BYTES == 64 * 1024


@pytest.mark.parametrize("field", ["input", "context"])
def test_a_value_exactly_at_the_cap_is_accepted(field: str) -> None:
    value = padded(MAX_RUN_JSON_BYTES)
    assert len(json.dumps(value)) == MAX_RUN_JSON_BYTES
    RunBody.model_validate(body(**{field: value}))


@pytest.mark.parametrize("field", ["input", "context"])
def test_a_value_one_byte_over_the_cap_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        RunBody.model_validate(body(**{field: padded(MAX_RUN_JSON_BYTES + 1)}))


def test_the_cap_counts_bytes_not_characters() -> None:
    wide = {"x": "é" * (MAX_RUN_JSON_BYTES // 2)}  # two bytes each once encoded
    with pytest.raises(ValidationError):
        RunBody.model_validate(body(context=wide))


def test_a_deeply_nested_value_is_measured_too() -> None:
    nested: dict[str, Any] = {"a": "x" * 40_000, "b": {"c": "y" * 40_000}}
    with pytest.raises(ValidationError):
        RunBody.model_validate(body(input=nested))


def test_the_pending_question_is_capped_as_well() -> None:
    with pytest.raises(ValidationError):
        RunBody.model_validate(body(pending_question=padded(MAX_RUN_JSON_BYTES + 1)))
