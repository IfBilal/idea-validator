"""The UI verdict fixtures are real M11 output: valid against the public schema, reproducible."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "services" / "ui" / "tests" / "fixtures"
SCHEMAS = ROOT / "schemas"


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "make_ui_fixtures", ROOT / "eval" / "verdict" / "make_ui_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _generator()


def _validator() -> Draft202012Validator:
    registry = Registry().with_resources(
        [
            (p.name, Resource.from_contents(json.loads(p.read_text()), DRAFT202012))
            for p in SCHEMAS.glob("*.schema.json")
        ]
    )
    schema = json.loads((SCHEMAS / "verdict.schema.json").read_text())
    return Draft202012Validator(schema, registry=registry)


@pytest.mark.parametrize("name", sorted(GEN.CASES))
def test_fixture_validates_against_the_public_verdict_schema(name: str) -> None:
    card = json.loads((FIXTURES / f"{name}.json").read_text())
    errors = list(_validator().iter_errors(card))
    assert not errors, [e.message for e in errors[:3]]


@pytest.mark.parametrize("name", sorted(GEN.CASES))
def test_fixture_is_what_the_engine_produces_today(name: str) -> None:
    committed = json.loads((FIXTURES / f"{name}.json").read_text())
    fresh = json.loads(json.dumps(GEN.card_for(GEN.CASES[name]), sort_keys=True))
    assert committed == fresh, (
        f"{name} is stale: run `uv run python eval/verdict/make_ui_fixtures.py`"
    )


def test_the_fixtures_cover_every_verdict_and_the_not_evaluated_gate_state() -> None:
    cards = [json.loads((FIXTURES / f"{n}.json").read_text()) for n in GEN.CASES]
    assert {c["verdict"] for c in cards} == {"GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"}
    assert any("not_evaluated" in c["gates"].values() for c in cards)
    assert any(c["score"] is None for c in cards)
    assert any(c["verdict"] == "NO-GO" and "fail" not in c["gates"].values() for c in cards)
