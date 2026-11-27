"""POST /runs bodies for every kind are validated strictly, with boundary values."""

from typing import Any

import pytest
from pydantic import ValidationError

from svc_orchestrator.models.api import (
    ChallengeInput,
    DiscoverInput,
    StartRun,
    ValidateInput,
    WhatIfInput,
)


def start(kind: str, input_: dict[str, Any], **over: Any) -> StartRun:
    return StartRun.model_validate({"kind": kind, "user_id": "u1", "input": input_, **over})


def discover(**over: Any) -> dict[str, Any]:
    return {"niche": "devtools", "count": 5, **over}


def challenge(**over: Any) -> dict[str, Any]:
    return {"idea_id": "idea_1", "version": 1, "claim_id": "cl_risk", **over}


def whatif(**over: Any) -> dict[str, Any]:
    return {"idea_id": "idea_1", "version": 1, "change_id": "chg_1", **over}


# ---- discover ------------------------------------------------------------------------------------
def test_a_discover_start_parses_into_a_typed_input() -> None:
    body = start("discover", discover())
    assert isinstance(body.input, DiscoverInput)
    assert (body.input.niche, body.input.count, body.input.deep_search) == ("devtools", 5, False)


@pytest.mark.parametrize("count", [1, 5, 10])
def test_discover_count_boundaries_are_accepted(count: int) -> None:
    assert start("discover", discover(count=count)).input.count == count  # type: ignore[union-attr]


@pytest.mark.parametrize("count", [0, -1, 11, 100, True, 2.5, "5", None])
def test_discover_count_outside_one_to_ten_is_rejected(count: Any) -> None:
    with pytest.raises(ValidationError):
        start("discover", discover(count=count))


def test_discover_count_defaults_to_five_and_niche_is_optional() -> None:
    body = start("discover", {})
    assert isinstance(body.input, DiscoverInput)
    assert (body.input.count, body.input.niche) == (5, None)


@pytest.mark.parametrize("niche", ["", "   ", "x" * 201, 5])
def test_discover_niche_must_be_a_short_non_blank_string(niche: Any) -> None:
    with pytest.raises(ValidationError):
        start("discover", discover(niche=niche))


def test_discover_niche_of_exactly_200_chars_is_accepted() -> None:
    assert start("discover", discover(niche="x" * 200)).input.niche == "x" * 200  # type: ignore[union-attr]


@pytest.mark.parametrize("flag", ["yes", 1, None])
def test_discover_deep_search_must_be_a_boolean(flag: Any) -> None:
    with pytest.raises(ValidationError):
        start("discover", discover(deep_search=flag))


def test_discover_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        start("discover", discover(idea_text="smuggled"))


# ---- challenge -----------------------------------------------------------------------------------
def test_a_challenge_start_parses_into_a_typed_input() -> None:
    body = start("challenge", challenge())
    assert isinstance(body.input, ChallengeInput)
    assert (body.input.idea_id, body.input.version, body.input.claim_id) == (
        "idea_1",
        1,
        "cl_risk",
    )


@pytest.mark.parametrize("version", [0, -1, True, 1.5, "1", None])
def test_challenge_version_must_be_a_positive_integer(version: Any) -> None:
    with pytest.raises(ValidationError):
        start("challenge", challenge(version=version))


@pytest.mark.parametrize("bad", ["", "a/b", "../x", "a b", "x" * 65, "idea?x", "idea\n1", 5])
@pytest.mark.parametrize("field", ["idea_id", "claim_id"])
def test_ids_that_go_into_paths_are_restricted(field: str, bad: Any) -> None:
    with pytest.raises(ValidationError):
        start("challenge", challenge(**{field: bad}))


@pytest.mark.parametrize("ok", ["a", "idea_1", "Idea-1_x", "x" * 64])
def test_id_boundaries_are_accepted(ok: str) -> None:
    assert start("challenge", challenge(idea_id=ok)).input.idea_id == ok  # type: ignore[union-attr]


# ---- what-if -------------------------------------------------------------------------------------
def test_a_whatif_with_a_change_id_is_accepted() -> None:
    body = start("whatif", whatif())
    assert isinstance(body.input, WhatIfInput)
    assert body.input.change_id == "chg_1" and body.input.custom_change is None


def test_a_whatif_with_a_custom_change_is_accepted() -> None:
    body = start("whatif", {"idea_id": "idea_1", "version": 2, "custom_change": "Add offline mode"})
    assert isinstance(body.input, WhatIfInput)
    assert body.input.custom_change == "Add offline mode"


def test_a_whatif_needs_exactly_one_of_change_id_and_custom_change() -> None:
    with pytest.raises(ValidationError):
        start("whatif", {"idea_id": "idea_1", "version": 1})
    with pytest.raises(ValidationError):
        start("whatif", whatif(custom_change="and also this"))


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001])
def test_a_custom_change_must_be_one_to_a_thousand_characters(text: str) -> None:
    with pytest.raises(ValidationError):
        start("whatif", {"idea_id": "idea_1", "version": 1, "custom_change": text})


def test_a_custom_change_of_exactly_a_thousand_characters_is_accepted() -> None:
    body = start("whatif", {"idea_id": "idea_1", "version": 1, "custom_change": "x" * 1000})
    assert isinstance(body.input, WhatIfInput) and len(body.input.custom_change or "") == 1000


@pytest.mark.parametrize("version", [0, -3, True, 2.0])
def test_a_whatif_version_must_be_a_positive_integer(version: Any) -> None:
    with pytest.raises(ValidationError):
        start("whatif", whatif(version=version))


# ---- kind and input must agree ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "input_"),
    [
        ("discover", {"idea_text": "x", "idea_id": "i", "version": 1}),
        ("validate", discover()),
        ("challenge", whatif()),
        ("whatif", challenge()),
        ("validate", challenge()),
    ],
)
def test_an_input_for_another_kind_is_rejected(kind: str, input_: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        start(kind, input_)


@pytest.mark.parametrize("kind", ["", "Validate", "replay", "eval", None, 3])
def test_unknown_kinds_are_rejected(kind: Any) -> None:
    with pytest.raises(ValidationError):
        start(kind, {})


def test_a_missing_user_or_input_is_rejected() -> None:
    for body in ({"kind": "discover", "input": {}}, {"kind": "discover", "user_id": "u"}):
        with pytest.raises(ValidationError):
            StartRun.model_validate(body)


def test_an_input_that_is_not_an_object_is_rejected() -> None:
    junk_values: list[Any] = ["x", 5, [], None]
    for junk in junk_values:
        with pytest.raises(ValidationError):
            StartRun.model_validate({"kind": "discover", "user_id": "u", "input": junk})


def test_validate_ids_are_restricted_because_they_reach_cache_and_m2_paths() -> None:
    base = {"idea_text": "x", "idea_id": "idea_1", "version": 1}
    ValidateInput(**base)
    for bad in ("a/b", "../x", "", "x" * 65, "a?b"):
        with pytest.raises(ValidationError):
            ValidateInput(**{**base, "idea_id": bad})
