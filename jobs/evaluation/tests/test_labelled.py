import json
from pathlib import Path
from typing import Any

import pytest
from evaluation.labelled import LabelledSetError, load_labelled

PROFILE = {
    "user_id": "x",
    "skills": ["python"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "portfolio",
    "interests": ["a"],
    "version": 1,
}


def item(**over: Any) -> dict[str, Any]:
    base = {"id": "L1", "idea_text": "an idea", "profile": PROFILE, "human_verdict": "GO"}
    return {**base, **over}


def write(tmp_path: Path, *rows: Any) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")
    return path


def test_loads_in_file_order_and_strips_profile_identity(tmp_path: Path) -> None:
    items = load_labelled(write(tmp_path, item(id="B"), "", item(id="A", human_verdict="NO-GO")))
    assert [i.id for i in items] == ["B", "A"]
    body = items[0].profile_body()
    assert "user_id" not in body and "version" not in body and body["hours_per_week"] == 10


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(LabelledSetError, match="not found"):
        load_labelled(tmp_path / "labelled_set.jsonl")


def test_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LabelledSetError, match="empty"):
        load_labelled(write(tmp_path, ""))


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("{not json", "line 1|:1:.*not valid JSON"),
        (item(human_verdict="MAYBE"), "human_verdict"),
        (item(idea_text="  "), "idea_text"),
        (item(idea_text="x" * 4001), "longer than"),
        (item(id=""), "'id'"),
        (item(profile={"skills": []}), "missing"),
        (item(profile="no"), "profile"),
        (item(poisoned="yes"), "poisoned"),
        (item(injection_forces="WIN"), "injection_forces"),
        (item(estimated_effort_hours=-1), "estimated_effort_hours"),
        (item(estimated_effort_hours=True), "estimated_effort_hours"),
        ([1], "JSON object"),
    ],
)
def test_bad_rows_name_the_problem(tmp_path: Path, row: Any, message: str) -> None:
    with pytest.raises(LabelledSetError, match=message):
        load_labelled(write(tmp_path, row))


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(LabelledSetError, match="duplicate"):
        load_labelled(write(tmp_path, item(), item()))


def test_boundaries_of_idea_length_and_effort(tmp_path: Path) -> None:
    ok = load_labelled(write(tmp_path, item(idea_text="x" * 4000, estimated_effort_hours=10000)))
    assert ok[0].estimated_effort_hours == 10000.0
