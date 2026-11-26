"""Every vector in eval/verdict/test_vectors.json must be reproduced exactly (spec 08 header).

``what_would_make_go`` is checked by the what-if tests; here it is excluded.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine import evaluate
from svc_verdict.services.engine_config import EngineConfig

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[4] / "eval" / "verdict" / "test_vectors.json").read_text()
)


def test_there_are_vectors_to_check() -> None:
    assert len(VECTORS) >= 24


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"][:60] for v in VECTORS])
def test_engine_reproduces_the_reference_vector(vector: dict[str, Any], cfg: EngineConfig) -> None:
    got = evaluate(EngineInput.model_validate(vector["input"]), cfg).to_dict()
    expected = {k: v for k, v in vector["expected"].items() if k != "what_would_make_go"}
    assert got == expected
