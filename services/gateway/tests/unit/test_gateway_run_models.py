import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from svc_gateway.models.runs import (
    AnswerBody,
    ApproveBody,
    ChallengeBody,
    DiscoverBody,
    ValidateBody,
    WhatIfBody,
)

IDEA = str(uuid.uuid4())


def accepts(model: Any, **data: Any) -> Any:
    return model.model_validate(data)


def rejects(model: Any, **data: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


# ---- /validate: boundary values on idea_text (<= 4000) and title (<= 200)
@pytest.mark.parametrize("length", [1, 2, 3999, 4000])
def test_idea_text_inside_the_bound(length: int) -> None:
    assert accepts(ValidateBody, idea_text="a" * length, title="t").idea_text == "a" * length


@pytest.mark.parametrize("text", ["", " ", "\n\t  ", "a" * 4001])
def test_idea_text_outside_the_bound_or_blank(text: str) -> None:
    rejects(ValidateBody, idea_text=text, title="t")


@pytest.mark.parametrize("length", [1, 199, 200])
def test_title_inside_the_bound(length: int) -> None:
    accepts(ValidateBody, idea_text="x", title="t" * length)


@pytest.mark.parametrize("title", ["", "  ", "t" * 201, "two\nlines", "tab\there", "nul\x00"])
def test_bad_titles(title: str) -> None:
    rejects(ValidateBody, idea_text="x", title=title)


def test_idea_text_may_have_newlines_and_tabs_but_not_other_control_chars() -> None:
    assert accepts(ValidateBody, idea_text="a\nb\tc\r\nd", title="t")
    rejects(ValidateBody, idea_text="bad\x00", title="t")
    rejects(ValidateBody, idea_text="bad\x1b[31m", title="t")


def test_a_new_idea_needs_a_title_but_an_existing_one_does_not() -> None:
    rejects(ValidateBody, idea_text="x")
    rejects(ValidateBody, idea_text="x", title=None, idea_id=None)
    assert accepts(ValidateBody, idea_text="x", idea_id=IDEA).title is None


@pytest.mark.parametrize("idea_id", ["", "abc", "123", "../x", IDEA + "x", 5, []])
def test_idea_id_must_be_a_uuid(idea_id: Any) -> None:
    rejects(ValidateBody, idea_text="x", title="t", idea_id=idea_id)


def test_unknown_fields_are_refused_everywhere() -> None:
    rejects(ValidateBody, idea_text="x", title="t", user_id=IDEA)
    rejects(DiscoverBody, niche=None, role="admin")
    rejects(AnswerBody, question_id="q", answer=1, extra=1)
    rejects(ApproveBody, approval_id="a", decision="approve", extra=1)
    rejects(ChallengeBody, version=1, claim_id="c", user_id=IDEA)
    rejects(WhatIfBody, version=1, change_id="c", extra=1)


def test_idea_text_is_stripped() -> None:
    assert accepts(ValidateBody, idea_text="  hi \n", title="  t ").title == "t"


# ---- /discover: count 1..10, strict types
@pytest.mark.parametrize(
    ("count", "ok"), [(0, False), (1, True), (5, True), (10, True), (11, False)]
)
def test_discover_count_boundaries(count: int, ok: bool) -> None:
    if ok:
        assert accepts(DiscoverBody, count=count).count == count
    else:
        rejects(DiscoverBody, count=count)


@pytest.mark.parametrize("count", [True, False, "5", 5.5, None, -1])
def test_discover_count_must_be_a_real_integer(count: Any) -> None:
    rejects(DiscoverBody, count=count)


def test_discover_defaults_and_flags() -> None:
    body = accepts(DiscoverBody)
    assert (body.niche, body.count, body.deep_search) == (None, 5, False)
    assert accepts(DiscoverBody, niche="devtools", deep_search=True).deep_search is True
    rejects(DiscoverBody, deep_search="yes")
    rejects(DiscoverBody, deep_search=1)


@pytest.mark.parametrize("niche", ["", "   ", "n" * 81, "a\nb"])
def test_discover_niche_validation(niche: str) -> None:
    rejects(DiscoverBody, niche=niche)


def test_discover_niche_boundary() -> None:
    assert accepts(DiscoverBody, niche="n" * 80)


# ---- answer: numeric (M3 asks numeric questions), <= 1000 chars when sent as text
@pytest.mark.parametrize(
    ("raw", "value"),
    [(5, 5.0), (0, 0.0), (-2.5, -2.5), ("120", 120.0), (" 7.5 ", 7.5), ("1e2", 100.0)],
)
def test_numeric_answers(raw: Any, value: float) -> None:
    assert accepts(AnswerBody, question_id="q1", answer=raw).answer == value


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "12 hours",
        True,
        None,
        [],
        {},
        float("nan"),
        float("inf"),
        "nan",
        "inf",
        "1" * 1001,
    ],
)
def test_non_numeric_answers_are_refused(raw: Any) -> None:
    rejects(AnswerBody, question_id="q1", answer=raw)


def test_answer_text_boundary_is_1000_characters() -> None:
    assert accepts(AnswerBody, question_id="q", answer="0" * 1000).answer == 0.0
    rejects(AnswerBody, question_id="q", answer="0" * 1001)


@pytest.mark.parametrize("question_id", ["", "q" * 81, "a b", "a/b", "../x", "q\n1", "q?x=1"])
def test_opaque_ids_are_constrained(question_id: str) -> None:
    rejects(AnswerBody, question_id=question_id, answer=1)
    rejects(ApproveBody, approval_id=question_id, decision="approve")


def test_opaque_id_boundary() -> None:
    accepts(AnswerBody, question_id="q" * 80, answer=1)
    accepts(AnswerBody, question_id="q_1.a:b-c", answer=1)


@pytest.mark.parametrize("decision", ["", "yes", "APPROVE", "approved", None, True])
def test_bad_decisions(decision: Any) -> None:
    rejects(ApproveBody, approval_id="a", decision=decision)


@pytest.mark.parametrize("decision", ["approve", "decline"])
def test_good_decisions(decision: str) -> None:
    assert accepts(ApproveBody, approval_id="a", decision=decision).decision == decision


# ---- challenge and what-if
@pytest.mark.parametrize("version", [0, -1, "1", 1.5, True, None])
def test_version_must_be_a_positive_integer(version: Any) -> None:
    rejects(ChallengeBody, version=version, claim_id="c")
    rejects(WhatIfBody, version=version, change_id="c")


def test_version_one_is_valid() -> None:
    accepts(ChallengeBody, version=1, claim_id="c")
    accepts(WhatIfBody, version=1, change_id="c")


def test_whatif_needs_exactly_one_of_change_id_and_custom_change() -> None:
    rejects(WhatIfBody, version=1)
    rejects(WhatIfBody, version=1, change_id="c", custom_change="x")
    assert accepts(WhatIfBody, version=1, custom_change="add a team of 4")


@pytest.mark.parametrize(("length", "ok"), [(1, True), (1000, True), (1001, False), (0, False)])
def test_custom_change_boundary(length: int, ok: bool) -> None:
    if ok:
        accepts(WhatIfBody, version=1, custom_change="c" * length)
    else:
        rejects(WhatIfBody, version=1, custom_change="c" * length)


def test_blank_custom_change_and_control_chars_are_refused() -> None:
    rejects(WhatIfBody, version=1, custom_change="   ")
    rejects(WhatIfBody, version=1, custom_change="a\x00b")
