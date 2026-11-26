"""Fails if a Pydantic model and its committed JSON Schema (schemas/*.schema.json) disagree.

The JSON Schema files are the source of truth (CLAUDE.md rule 3); the models mirror them.
Both are reduced to a canonical form (types, enums, bounds, required, nesting) before comparing,
so cosmetic differences (titles, defaults, $ref layout) do not matter but real drift does.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from common.schemas import (
    DIMENSIONS,
    ClaimCheckRequest,
    ClaimCheckResponse,
    DimensionScore,
    EvidenceItem,
    FetchRequest,
    FetchResponse,
    Gates,
    IdeaCard,
    RunEvent,
    ScoreRequest,
    Sensitivity,
    StudentProfile,
    Thresholds,
    TraceRecord,
    Verdict,
    Weights,
)

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"

KEPT = (
    "pattern", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "format",
)  # fmt: skip

CASES: list[tuple[str, str | None, type[BaseModel]]] = [
    ("student_profile", None, StudentProfile),
    ("idea_card", None, IdeaCard),
    ("evidence_item", None, EvidenceItem),
    ("dimension_score", None, DimensionScore),
    ("score_request", None, ScoreRequest),
    ("verdict", None, Verdict),
    ("trace_record", None, TraceRecord),
    ("run_event", None, RunEvent),
    ("fetch", "request", FetchRequest),
    ("fetch", "response", FetchResponse),
    ("claim_check", "request", ClaimCheckRequest),
    ("claim_check", "response", ClaimCheckResponse),
]


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    return data


def _canon(node: dict[str, Any], root: dict[str, Any], required: bool = True) -> dict[str, Any]:
    """Reduce a JSON Schema fragment to the parts that define the contract."""
    if "$ref" in node:
        ref: str = node["$ref"]
        if ref.startswith("#/"):
            target: Any = root
            for part in ref[2:].split("/"):
                target = target[part]
            return _canon(target, root, required)
        other = _load(ref.removesuffix(".schema.json"))
        return _canon(other, other, required)
    if "allOf" in node and len(node["allOf"]) == 1:
        return _canon(node["allOf"][0], root, required)
    if "anyOf" in node:
        branches = node["anyOf"]
        non_null = [b for b in branches if b != {"type": "null"}]
        assert len(non_null) == 1, f"unsupported anyOf: {node}"
        inner = _canon(non_null[0], root, required)
        # an absent optional property is equivalent to null, so only required ones keep it
        if len(non_null) != len(branches) and required:
            inner["nullable"] = True
        return inner
    out: dict[str, Any] = {}
    if "const" in node:
        out["enum"] = [node["const"]]
    elif "enum" in node:
        out["enum"] = sorted(node["enum"])
    elif "type" in node:
        out["type"] = node["type"]
    for key in KEPT:
        if key in node:
            out[key] = node[key]
    if "items" in node:
        out["items"] = _canon(node["items"], root)
    if "properties" in node:
        req = set(node.get("required", []))
        out["properties"] = {
            name: _canon(sub, root, name in req) for name, sub in node["properties"].items()
        }
        out["required"] = sorted(req)
        out["additionalProperties"] = node.get("additionalProperties", True) is not False
    return out


def _json_schema(name: str, sub: str | None) -> dict[str, Any]:
    schema = _load(name)
    return schema["$defs"][sub] if sub else schema


@pytest.mark.parametrize(("name", "sub", "model"), CASES)
def test_model_matches_json_schema(name: str, sub: str | None, model: type[BaseModel]) -> None:
    committed = _json_schema(name, sub)
    generated = model.model_json_schema(mode="validation")
    assert _canon(generated, generated) == _canon(committed, _load(name))


def _registry() -> Registry:
    resources = [
        (f"{p.name}", Resource.from_contents(json.loads(p.read_text()), DRAFT202012))
        for p in SCHEMAS.glob("*.schema.json")
    ]
    return Registry().with_resources(resources)


EXAMPLES = [
    ("student_profile", "student_profile", StudentProfile),
    ("idea_card", "idea_card", IdeaCard),
    ("evidence_item", "evidence_item", EvidenceItem),
    ("dimension_score", "dimension_score", DimensionScore),
    ("trace_record", "trace_record", TraceRecord),
]


@pytest.mark.parametrize(("example", "schema", "model"), EXAMPLES)
def test_examples_validate_against_schema_and_model(
    example: str, schema: str, model: type[BaseModel]
) -> None:
    payload = json.loads((SCHEMAS / "examples" / f"{example}.json").read_text())
    validator = Draft202012Validator(_load(schema), registry=_registry())
    errors = sorted(validator.iter_errors(payload), key=str)
    assert not errors, errors
    parsed = model.model_validate(payload)
    assert model.model_validate(parsed.model_dump(by_alias=True, mode="json")) == parsed


def test_unknown_fields_are_rejected() -> None:
    payload = json.loads((SCHEMAS / "examples" / "student_profile.json").read_text())
    with pytest.raises(ValueError, match="Extra inputs"):
        StudentProfile.model_validate({**payload, "surprise": 1})


def test_worked_example_core_fields_match_models() -> None:
    """The worked example is raw engine output (``adjusted_scores`` dict, no ``scores`` list).

    The full API payload adds the per-dimension ``scores`` list, so only the shared parts are
    checked here; the full ``Verdict`` is exercised by the M11 tests in Phase 2.
    """
    payload = json.loads((SCHEMAS / "examples" / "verdict_worked_example.json").read_text())
    assert Gates.model_validate(payload["gates"])
    assert Weights.model_validate(payload["weights"])
    assert Thresholds.model_validate(payload["thresholds"])
    assert Sensitivity.model_validate(payload["sensitivity"])
    assert payload["verdict"] in {"GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"}
    assert set(payload["adjusted_scores"]) == set(DIMENSIONS)
