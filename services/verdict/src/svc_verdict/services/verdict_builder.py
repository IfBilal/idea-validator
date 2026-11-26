"""Turn an engine evaluation into the public ``Verdict`` payload (schemas/verdict.schema.json)."""

from common.schemas import (
    DimensionScore,
    Gates,
    Sensitivity,
    Thresholds,
    Verdict,
    VerdictVersions,
    Weights,
    WhatWouldMakeGo,
)
from common.schemas.types import DIMENSIONS

from ..models.api import VerdictRequest
from ..models.results import EvaluationResult, SensitivityResult
from .engine import adjust_for_challenges
from .engine_config import EngineConfig


def missing_items(req: VerdictRequest, cfg: EngineConfig, result: EvaluationResult) -> list[str]:
    """What an INSUFFICIENT_EVIDENCE verdict is missing (U10)."""
    items: list[str] = []
    gi = req.gate_inputs
    if result.gates["evidence"] == "fail":
        if gi.n_sources < cfg.gates.min_sources:
            items.append(f"{cfg.gates.min_sources - gi.n_sources} more independent source(s)")
        if gi.n_platforms < cfg.gates.min_platforms:
            items.append(f"{cfg.gates.min_platforms - gi.n_platforms} more platform(s)")
        return items
    items += [f"evidence for {d}" for d, v in result.adjusted_scores.items() if v is None]
    if not items:
        _, confs, _ = adjust_for_challenges(req.to_engine_input(), cfg)
        items = [f"stronger evidence for {d}" for d in DIMENSIONS if confs[d] < 0.5]
    return items or ["more independent sources"]


def build_verdict(req: VerdictRequest, cfg: EngineConfig, result: EvaluationResult) -> Verdict:
    inp = req.to_engine_input()
    _, confs, _ = adjust_for_challenges(inp, cfg)
    by_dim: dict[str, DimensionScore] = {s.dimension: s for s in req.scores}
    scores = []
    for d in DIMENSIONS:
        original = by_dim[d]
        adjusted = result.adjusted_scores[d]
        scores.append(
            original.model_copy(
                update={
                    "score": adjusted,
                    "confidence": confs[d],
                    "abstained": adjusted is None,
                    "adjusted_by_challenge": 0.0
                    if adjusted is None or original.score is None
                    else round(adjusted - original.score, 4),
                }
            )
        )
    return Verdict(
        idea_id=req.idea_id,
        version=req.version,
        run_id=req.run_id,
        verdict=result.verdict,
        stability=result.stability,
        score=result.score,
        confidence=result.confidence,
        gates=Gates(**result.gates),
        weights=Weights(**result.weights),
        thresholds=Thresholds(**result.thresholds),
        scores=scores,
        weakest_dimension=result.weakest_dimension,
        what_would_make_go=[
            WhatWouldMakeGo.model_validate(c.model_dump(by_alias=True, exclude_unset=True))
            for c in (result.what_would_make_go or [])
        ],
        sensitivity=_sensitivity(result.sensitivity),
        reasons=result.reasons,
        missing=missing_items(req, cfg, result)
        if result.verdict == "INSUFFICIENT_EVIDENCE"
        else None,
        versions=VerdictVersions(**result.versions),
    )


def _sensitivity(sens: SensitivityResult) -> Sensitivity:
    flips = [f.model_dump() for f in sens.flips]
    if sens.note is None:  # the schema allows `note` only as a string, so leave it unset
        return Sensitivity(runs=sens.runs, flipped=sens.flipped, flips=flips)
    return Sensitivity(runs=sens.runs, flipped=sens.flipped, flips=flips, note=sens.note)
