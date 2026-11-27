import pytest

from common.cache_keys import idea_hash, verdict_cache_key

BASE = {
    "idea_id": "i1",
    "idea_version": 2,
    "idea_text": "A tool for notes",
    "profile_version": 3,
    "rubrics_version": "r1",
    "weights_version": "w1",
    "engine_version": "m11@v1",
}


def test_key_has_the_documented_shape() -> None:
    key = verdict_cache_key(**BASE)  # type: ignore[arg-type]
    assert key == f"i1:2:{idea_hash('A tool for notes')}:p3:rr1:ww1:em11@v1"


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("idea_id", "i2"),
        ("idea_version", 3),
        ("idea_text", "A different idea"),
        ("profile_version", 4),
        ("rubrics_version", "r2"),
        ("weights_version", "w2"),
        ("engine_version", "m11@v2"),
    ],
)
def test_changing_any_version_changes_the_key(field: str, changed: object) -> None:
    original = verdict_cache_key(**BASE)  # type: ignore[arg-type]
    assert verdict_cache_key(**{**BASE, field: changed}) != original  # type: ignore[arg-type]


def test_the_idea_hash_ignores_case_and_whitespace_only() -> None:
    assert idea_hash("A  Tool\nfor notes ") == idea_hash("a tool for notes")
    assert idea_hash("a tool for notes") != idea_hash("a tool for note")
