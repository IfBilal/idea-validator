"""The set of data sources is one decision (ADR-009) that must hold in every schema and type at once."""

import json
from pathlib import Path
from typing import get_args

import pytest

from common.schemas.types import Source

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = {"hackernews", "github", "lobsters", "stackexchange", "reddit", "producthunt", "yc"}


def _enum(schema_file: str) -> set[str]:
    schema = json.loads((ROOT / "schemas" / schema_file).read_text())
    found = schema["properties"]["source"]["enum"]
    return set(found)


def test_the_source_type_lists_the_keyless_sources_chosen_in_adr_009() -> None:
    assert set(get_args(Source)) == EXPECTED


@pytest.mark.parametrize("schema_file", ["evidence_item.schema.json", "idea_card.schema.json"])
def test_every_json_schema_accepts_exactly_the_same_sources(schema_file: str) -> None:
    assert _enum(schema_file) == EXPECTED


def test_the_data_model_doc_lists_the_same_platforms() -> None:
    text = (ROOT / "docs" / "harness" / "04_DATA_MODEL.md").read_text()
    for source in EXPECTED:
        assert f"'{source}'" in text, f"04_DATA_MODEL.md does not mention {source}"
