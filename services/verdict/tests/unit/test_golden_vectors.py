"""Every vector in eval/verdict/test_vectors.json must be reproduced exactly (spec 08 header)."""

import json
from pathlib import Path
from typing import Any

import pytest

from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine_config import EngineConfig
from svc_verdict.services.whatif import evaluate_with_whatif

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[4] / "eval" / "verdict" / "test_vectors.json").read_text()
)


def test_there_are_vectors_to_check() -> None:
    assert len(VECTORS) >= 24


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"][:60] for v in VECTORS])
def test_engine_reproduces_the_reference_vector(vector: dict[str, Any], cfg: EngineConfig) -> None:
    got = evaluate_with_whatif(EngineInput.model_validate(vector["input"]), cfg).to_dict()
    assert got == vector["expected"]
