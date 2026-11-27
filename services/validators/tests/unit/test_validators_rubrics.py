"""Strict rubric loading: every shipped rubric loads, malformed ones fail loudly."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from svc_validators.services.rubrics import RubricError, load_rubric, load_rubrics

ROOT = Path(__file__).resolve().parents[4]
RUBRICS = ROOT / "rubrics"
DIMENSIONS = ["demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"]
PINS = dict.fromkeys(DIMENSIONS, 1)


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_every_shipped_rubric_loads(dimension: str) -> None:
    rubric = load_rubric(RUBRICS, dimension, 1)
    assert rubric.id == f"{dimension}@v1"
    assert rubric.dimension == dimension
    assert sorted(rubric.anchors) == [0, 1, 2, 3, 4, 5]
    assert rubric.feature_prior[-1].when == "true"
    assert rubric.features


def test_load_rubrics_returns_all_six_pinned() -> None:
    rubrics = load_rubrics(RUBRICS, PINS)
    assert set(rubrics) == set(DIMENSIONS)


def _valid() -> dict[str, Any]:
    return {
        "dimension": "risk",
        "version": 1,
        "id": "risk@v1",
        "scale": "0-5",
        "description": "d",
        "anchors": {i: f"anchor {i}" for i in range(6)},
        "features": ["risk_flags"],
        "feature_prior": [
            {"when": "len(risk_flags) == 0", "score": 5},
            {"when": "true", "score": 1},
        ],
        "evidence_rule": "cite",
        "output_schema": "score, confidence, evidence_ids, reason",
        "examples": [],
    }


def _write(tmp_path: Path, data: dict[str, Any] | str, name: str = "risk@v1.yaml") -> None:
    text = data if isinstance(data, str) else yaml.safe_dump(data)
    (tmp_path / name).write_text(text)


def test_valid_minimal_rubric_loads(tmp_path: Path) -> None:
    _write(tmp_path, _valid())
    assert load_rubric(tmp_path, "risk", 1).feature_prior[0].score == 5


def _mutations() -> list[tuple[str, dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any]]] = []

    def case(name: str, **changes: Any) -> None:
        data = _valid()
        for key, value in changes.items():
            if value is _DELETE:
                del data[key]
            else:
                data[key] = value
        cases.append((name, data))

    case("missing anchor 3", anchors={i: "a" for i in range(6) if i != 3})
    case("extra anchor 6", anchors={i: "a" for i in range(7)})
    case("negative anchor", anchors={**{i: "a" for i in range(6)}, -1: "a"})
    case("blank anchor", anchors={**{i: "a" for i in range(6)}, 2: " "})
    case("wrong dimension", dimension="learning")
    case("unknown dimension", dimension="verdict", id="verdict@v1")
    case("id mismatch", id="risk@v2")
    case("version mismatch", version=2)
    case("version zero", version=0, id="risk@v0")
    case("scale", scale="0-10")
    case("unknown key", extra_section={"a": 1})
    case("missing evidence rule", evidence_rule=_DELETE)
    case("missing features", features=_DELETE)
    case("empty prior", feature_prior=[])
    case("bad expression", feature_prior=[{"when": "__import__('os')", "score": 1}, _TRUE])
    case("score above 5", feature_prior=[{"when": "true", "score": 5.5}])
    case("score below 0", feature_prior=[{"when": "true", "score": -1}])
    case("prior not ending in true", feature_prior=[{"when": "len(risk_flags) == 0", "score": 5}])
    case("prior rule extra key", feature_prior=[{"when": "true", "score": 1, "x": 1}])
    case("prior rule missing score", feature_prior=[{"when": "true"}])
    case(
        "rule references undeclared feature", feature_prior=[{"when": "foo > 1", "score": 3}, _TRUE]
    )
    return cases


_DELETE = object()
_TRUE = {"when": "true", "score": 0}


@pytest.mark.parametrize("name,data", _mutations(), ids=[n for n, _ in _mutations()])
def test_malformed_rubrics_are_rejected(tmp_path: Path, name: str, data: dict[str, Any]) -> None:
    _write(tmp_path, data)
    with pytest.raises(RubricError):
        load_rubric(tmp_path, "risk", 1)


@pytest.mark.parametrize("text", ["", "- a list", "a: [unclosed", "just a string"])
def test_non_mapping_or_invalid_yaml_rejected(tmp_path: Path, text: str) -> None:
    _write(tmp_path, text)
    with pytest.raises(RubricError):
        load_rubric(tmp_path, "risk", 1)


def test_missing_file_is_a_rubric_error(tmp_path: Path) -> None:
    with pytest.raises(RubricError, match="not found"):
        load_rubric(tmp_path, "risk", 1)


def test_unpinned_version_is_not_loaded() -> None:
    # Only the exact pinned version is read; v2 does not exist in the repo.
    with pytest.raises(RubricError):
        load_rubric(RUBRICS, "risk", 2)


def test_every_dimension_must_be_pinned() -> None:
    pins = {k: v for k, v in PINS.items() if k != "risk"}
    with pytest.raises(RubricError, match="risk"):
        load_rubrics(RUBRICS, pins)


def test_pin_for_unknown_dimension_rejected() -> None:
    with pytest.raises(RubricError):
        load_rubrics(RUBRICS, {**PINS, "verdict": 1})
