"""Real M11 verdict cards for the UI tests: ``uv run python eval/verdict/make_ui_fixtures.py``.

Each fixture is produced by the real engine (``evaluate_with_whatif`` + ``build_verdict``) from a
golden vector's input, so it has exactly the public Verdict shape (``scores`` array, three gate
states, nullable fields) and not the reference engine's raw dict. ``test_ui_fixtures.py`` checks
that the committed files equal a fresh run and validate against ``schemas/verdict.schema.json``.
"""

import json
from pathlib import Path
from typing import Any

from svc_verdict.models.api import VerdictRequest
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine_config import load_engine_config
from svc_verdict.services.verdict_builder import build_verdict
from svc_verdict.services.whatif import evaluate_with_whatif

from common.schemas import DimensionScore

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "eval" / "verdict" / "test_vectors.json").read_text())
OUT = ROOT / "services" / "ui" / "tests" / "fixtures"

# fixture name -> vector index (see the "name" of each vector in test_vectors.json)
CASES: dict[str, int] = {
    "m11_go": 0,
    "m11_go_stable_what_if": 1,
    "m11_pivot_with_what_if": 23,
    "m11_pivot_floor_breach": 17,
    "m11_nogo_gate_feasibility": 5,
    "m11_nogo_gate_risk": 8,
    "m11_nogo_low_score_no_failed_gate": 16,
    "m11_insufficient_two_sources": 2,
    "m11_insufficient_risk_abstained": 11,
    "m11_insufficient_low_confidence": 12,
}


def card_for(index: int) -> dict[str, Any]:
    vector = VECTORS[index]
    engine_input = EngineInput.model_validate(vector["input"])
    valid_ids: dict[str, str] = {}  # the vectors use placeholder ids such as card_x#c0

    def real_id(raw: str) -> str:
        return valid_ids.setdefault(raw, f"card_{0x1A2B3C00 + len(valid_ids):08x}#c0")

    scores = [
        DimensionScore(
            dimension=s.dimension,
            score=s.score,
            confidence=s.confidence,
            evidence_ids=[real_id(i) for i in s.evidence_ids],
            reason=f"fixture reason for {s.dimension}",
            rubric_version=f"{s.dimension}@v1",
            model="fixture",
            abstained=s.score is None,
        )
        for s in engine_input.scores
    ]
    request = VerdictRequest(
        idea_id=engine_input.idea_id,
        version=engine_input.version,
        run_id=engine_input.run_id,
        profile=engine_input.profile,
        scores=scores,
        gate_inputs=engine_input.gate_inputs,
        objections_accepted=list(engine_input.objections_accepted),
    )
    cfg = load_engine_config(ROOT / "config")
    result = evaluate_with_whatif(engine_input, cfg)
    verdict = build_verdict(request, cfg, result)
    card: dict[str, Any] = verdict.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return card


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, index in CASES.items():
        (OUT / f"{name}.json").write_text(
            json.dumps(card_for(index), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"wrote {len(CASES)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
