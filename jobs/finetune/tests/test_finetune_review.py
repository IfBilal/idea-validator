import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from finetune.prompting import PromptFactory, make_example, repo_root
from finetune.review import (
    NotInteractiveError,
    ReviewRecord,
    example_key,
    merge_reviews,
    require_interactive,
    review_session,
)
from finetune.schema import validate_example

EV = [("card_8f21aa01#c2", "Users complain about slow exports")]


def ex(idea: str, score: float | None = 3.0, dim: str = "risk") -> dict[str, Any]:
    f = PromptFactory(repo_root() / "rubrics", repo_root() / "prompts")
    return make_example(
        f,
        idea_id=idea,
        idea=f"idea {idea}",
        rubric_id=f"{dim}@v1",
        evidence=EV if score is not None else [],
        features={},
        score=score,
        confidence=0.6 if score is not None else 0.0,
        cited=[EV[0][0]] if score is not None else [],
        reason="r" if score is not None else "no evidence",
    )


def scripted(*answers: str) -> Callable[[str], str]:
    it = iter(answers)
    return lambda _prompt: next(it)


def test_accept_records_original_equals_final(tmp_path: Path) -> None:
    out = tmp_path / "taimoor.jsonl"
    shown: list[str] = []
    n = review_session(
        [ex("a")],
        reviewer="taimoor",
        input_fn=scripted("a"),
        print_fn=shown.append,
        out_path=out,
        now_fn=lambda: "2026-11-27T10:00:00Z",
    )
    assert n == 1
    rec = json.loads(out.read_text().splitlines()[0])
    assert rec["checked_by"] == "taimoor"
    assert rec["original_label"] == 3.0 and rec["final_label"] == 3.0
    assert any("Users complain" in s for s in shown)  # evidence is shown
    assert any("idea a" in s for s in shown)


def test_edit_changes_final_label_only(tmp_path: Path) -> None:
    out = tmp_path / "r.jsonl"
    review_session(
        [ex("a")],
        reviewer="bilal",
        input_fn=scripted("e", "4.5"),
        print_fn=lambda _s: None,
        out_path=out,
        now_fn=lambda: "t",
    )
    rec = json.loads(out.read_text().splitlines()[0])
    assert rec["original_label"] == 3.0 and rec["final_label"] == 4.5


def test_invalid_score_is_reprompted(tmp_path: Path) -> None:
    out = tmp_path / "r.jsonl"
    review_session(
        [ex("a")],
        reviewer="bilal",
        input_fn=scripted("e", "9", "3.3", "x", "2"),
        print_fn=lambda _s: None,
        out_path=out,
        now_fn=lambda: "t",
    )
    assert json.loads(out.read_text().splitlines()[0])["final_label"] == 2.0


def test_skip_and_quit(tmp_path: Path) -> None:
    out = tmp_path / "r.jsonl"
    n = review_session(
        [ex("a"), ex("b"), ex("c")],
        reviewer="bilal",
        input_fn=scripted("s", "a", "q"),
        print_fn=lambda _s: None,
        out_path=out,
        now_fn=lambda: "t",
    )
    assert n == 1
    assert [json.loads(line)["idea_id"] for line in out.read_text().splitlines()] == ["b"]


def test_already_reviewed_by_this_reviewer_is_not_shown_again(tmp_path: Path) -> None:
    out = tmp_path / "r.jsonl"
    kw: dict[str, Any] = {
        "reviewer": "bilal",
        "print_fn": lambda _s: None,
        "out_path": out,
        "now_fn": lambda: "t",
    }
    review_session([ex("a")], input_fn=scripted("a"), **kw)
    n = review_session([ex("a"), ex("b")], input_fn=scripted("a"), **kw)
    assert n == 1
    assert len(out.read_text().splitlines()) == 2


def test_reviewer_name_must_be_known(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reviewer"):
        review_session(
            [ex("a")],
            reviewer="claude",
            input_fn=scripted("a"),
            print_fn=lambda _s: None,
            out_path=tmp_path / "x.jsonl",
            now_fn=lambda: "t",
        )


def test_cli_refuses_non_interactive_stdin() -> None:
    with pytest.raises(NotInteractiveError):
        require_interactive(is_tty=False)
    require_interactive(is_tty=True)


def rec(idea: str, who: str, orig: float | None, final: float | None) -> ReviewRecord:
    return ReviewRecord(
        idea_id=idea,
        dimension="risk",
        checked_by=who,
        checked_at="2026-11-27T10:00:00Z",
        original_label=orig,
        final_label=final,
    )


def test_merge_marks_hand_checked_and_updates_target() -> None:
    merged, conflicts = merge_reviews([ex("a")], [rec("a", "taimoor", 3.0, 4.0)])
    assert not conflicts
    v = validate_example(merged[0])
    assert v.meta.hand_checked is True
    assert v.meta.final_label == 4.0 and v.meta.level == 4.0
    assert json.loads(v.messages[2].content)["score"] == 4.0
    assert v.meta.checked_by == "taimoor"


def test_merge_two_agreeing_reviewers() -> None:
    merged, conflicts = merge_reviews(
        [ex("a")], [rec("a", "taimoor", 3.0, 3.5), rec("a", "bilal", 3.0, 3.5)]
    )
    assert not conflicts
    assert merged[0]["meta"]["checked_by"] == "bilal+taimoor"


def test_merge_disagreement_is_a_conflict_and_stays_unchecked() -> None:
    merged, conflicts = merge_reviews(
        [ex("a")], [rec("a", "taimoor", 3.0, 3.5), rec("a", "bilal", 3.0, 1.0)]
    )
    assert conflicts == [example_key("a", "risk")]
    assert merged[0]["meta"]["hand_checked"] is False


def test_merge_unreviewed_stay_false_and_unknown_review_ignored() -> None:
    merged, _ = merge_reviews([ex("a")], [rec("zzz", "taimoor", 3.0, 3.0)])
    assert merged[0]["meta"]["hand_checked"] is False


def test_final_abstain_label_rewrites_target_to_abstain() -> None:
    merged, _ = merge_reviews([ex("a")], [rec("a", "taimoor", 3.0, None)])
    body = json.loads(merged[0]["messages"][2]["content"])
    assert body["score"] is None and body["confidence"] == 0 and body["evidence_ids"] == []
    validate_example(merged[0])
