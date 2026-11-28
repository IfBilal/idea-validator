"""Sanity checks for the drafted labelled-set candidates (not the labels themselves)."""

import json
from collections import Counter
from pathlib import Path

import pytest

FILE = Path(__file__).with_name("candidates.jsonl")
ROWS = [json.loads(line) for line in FILE.read_text().splitlines() if line.strip()]
CATEGORIES = {"clear_go", "clear_nogo", "borderline", "thin_evidence"}
PROFILE_KEYS = {
    "user_id", "skills", "hours_per_week", "weeks_available", "team_size",
    "compute", "goal", "interests", "version",
}  # fmt: skip


def test_ids_are_unique_and_sequential() -> None:
    ids = [r["id"] for r in ROWS]
    assert ids == [f"L{i:03d}" for i in range(1, len(ids) + 1)]


def test_every_category_has_at_least_ten_candidates() -> None:
    counts = Counter(r["intended_category"] for r in ROWS)
    assert set(counts) == CATEGORIES
    assert all(n >= 10 for n in counts.values())
    assert len(ROWS) >= 40


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_each_profile_is_a_valid_student_profile_and_the_text_fits(
    row: dict[str, object],
) -> None:
    profile = row["profile"]
    assert isinstance(profile, dict)
    assert set(profile) == PROFILE_KEYS
    assert 0 <= profile["hours_per_week"] <= 80
    assert 1 <= profile["weeks_available"] <= 52
    assert 1 <= profile["team_size"] <= 10
    assert profile["compute"] in {"laptop", "free_gpu", "paid"}
    assert profile["goal"] in {"startup", "portfolio", "course_project"}
    assert len(profile["skills"]) <= 30 and len(profile["interests"]) <= 30
    text = row["idea_text"]
    assert isinstance(text, str) and 20 <= len(text) <= 4000


def test_candidates_carry_no_verdicts_yet() -> None:
    forbidden = {"human_verdict", "labels", "category"}
    assert not any(forbidden & set(r) for r in ROWS)


def test_same_idea_groups_use_different_profiles() -> None:
    groups: dict[str, list[dict[str, object]]] = {}
    for r in ROWS:
        if "idea_group" in r:
            groups.setdefault(str(r["idea_group"]), []).append(r)
    assert len(groups) >= 4
    for name, members in groups.items():
        assert len({m["idea_text"] for m in members}) == 1, name
        profiles = {json.dumps(m["profile"], sort_keys=True) for m in members}
        assert len(profiles) == len(members) >= 2, name


def test_new_candidates_are_unique_dev_ideas_with_hints() -> None:
    new = [r for r in ROWS if r["id"] >= "L044"]
    assert len(new) == 31
    assert len({r["id"] for r in new}) == 31
