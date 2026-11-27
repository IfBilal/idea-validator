"""Cache keys carry every version (CLAUDE.md rule 6); memo keys are stable input hashes."""

import pytest

from svc_orchestrator.services.cache_keys import (
    canonical_hash,
    idea_hash,
    memo_key,
    verdict_cache_key,
)

BASE = {
    "idea_id": "idea_1",
    "idea_version": 2,
    "idea_text": "A CLI that lints Dockerfiles",
    "profile_version": 3,
    "rubrics_version": "r1",
    "weights_version": "w1",
    "engine_version": "m11@v1",
}


def test_verdict_cache_key_has_the_documented_shape() -> None:
    key = verdict_cache_key(**BASE)  # type: ignore[arg-type]
    expected_hash = idea_hash(str(BASE["idea_text"]))
    assert key == f"verdict:idea_1:2:{expected_hash}:p3:rr1:ww1:em11@v1"


@pytest.mark.parametrize(
    ("field", "other"),
    [
        ("idea_id", "idea_2"),
        ("idea_version", 3),
        ("idea_text", "A different idea"),
        ("profile_version", 4),
        ("rubrics_version", "r2"),
        ("weights_version", "w2"),
        ("engine_version", "m11@v2"),
    ],
)
def test_changing_any_single_version_changes_the_key(field: str, other: object) -> None:
    changed = {**BASE, field: other}
    assert verdict_cache_key(**changed) != verdict_cache_key(**BASE)  # type: ignore[arg-type]


def test_idea_hash_ignores_whitespace_differences_only() -> None:
    assert idea_hash("a  b\n c") == idea_hash(" a b c ")
    assert idea_hash("a b c") != idea_hash("a b C")


@pytest.mark.parametrize("field", ["rubrics_version", "weights_version", "engine_version"])
def test_empty_version_strings_are_refused(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        verdict_cache_key(**{**BASE, field: ""})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["idea_version", "profile_version"])
def test_non_positive_versions_are_refused(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        verdict_cache_key(**{**BASE, field: 0})  # type: ignore[arg-type]


def test_canonical_hash_is_independent_of_key_order_and_stable() -> None:
    assert canonical_hash({"a": 1, "b": [1, 2]}) == canonical_hash({"b": [1, 2], "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})
    assert len(canonical_hash({})) == 64


def test_canonical_hash_distinguishes_list_order() -> None:
    assert canonical_hash([1, 2]) != canonical_hash([2, 1])


def test_memo_key_joins_step_and_a_short_hash() -> None:
    key = memo_key("evidence", {"q": "x"})
    assert key.startswith("evidence:")
    assert key == memo_key("evidence", {"q": "x"})
    assert key != memo_key("evidence", {"q": "y"})
    assert key != memo_key("features", {"q": "x"})
