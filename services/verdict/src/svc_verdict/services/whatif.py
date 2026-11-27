"""What-if search and value of information (docs/harness/08 §2 step 8, §6). Pure, no I/O."""

import math
from typing import Literal

from pydantic import ValidationError

from common.errors import ValidationFailed

from ..models.api import Change, Unknown, VoiPoint, VoiResponse
from ..models.inputs import EngineInput, GateInputs, ProfileIn, ScoreIn
from ..models.results import EvaluationResult, WhatIfCandidate
from .engine import DIMS, EPS, RANK, evaluate
from .engine_config import EngineConfig

Scope = Literal["score", "gate_input", "profile"]


def with_score(inp: EngineInput, dimension: str, value: float) -> EngineInput:
    changed = [
        s.model_copy(update={"score": value}) if s.dimension == dimension else s for s in inp.scores
    ]
    return inp.model_copy(update={"scores": changed})


def apply_value(inp: EngineInput, scope: Scope, field: str, value: float) -> EngineInput:
    """Copy of ``inp`` with one field replaced; invalid values raise ``ValidationFailed``."""
    try:
        if scope == "score":
            current = inp.score_of(field)
            checked = ScoreIn.model_validate({**current.model_dump(), "score": value})
            return with_score(inp, field, checked.score if checked.score is not None else value)
        if scope == "gate_input":
            gates = GateInputs.model_validate({**inp.gate_inputs.model_dump(), field: value})
            return inp.model_copy(update={"gate_inputs": gates})
        profile = ProfileIn.model_validate({**inp.profile.model_dump(), field: value})
    except ValidationError as exc:
        raise ValidationFailed(f"{field}={value} is not allowed", {"field": field}) from exc
    return inp.model_copy(update={"profile": profile})


def apply_change(inp: EngineInput, change: Change) -> EngineInput:
    if change.dimension is not None:
        return apply_value(inp, "score", change.dimension, change.to)
    assert change.profile_field is not None
    return apply_value(inp, "profile", change.profile_field, change.to)


def suggest(inp: EngineInput, cfg: EngineConfig, base: EvaluationResult) -> list[WhatIfCandidate]:
    """Smallest single change that improves the verdict; aim for a STABLE GO when possible."""
    if base.verdict == "GO" and base.stability == "STABLE":
        return []
    candidates: list[WhatIfCandidate] = []
    step = cfg.whatif.step
    for s in inp.scores:
        if s.score is None:
            continue
        k = 1
        while s.score + k * step <= 5.0 + EPS:
            target = s.score + k * step
            result = evaluate(with_score(inp, s.dimension, target), cfg)
            better = RANK[result.verdict] > RANK[base.verdict] or (
                result.verdict == base.verdict == "GO" and result.stability == "STABLE"
            )
            if better:
                candidates.append(
                    WhatIfCandidate(
                        dimension=s.dimension,
                        from_=s.score,
                        to=target,
                        delta=k * step,
                        new_score=result.score,
                        new_verdict=result.verdict,
                        stable=result.stability == "STABLE",
                    )
                )
                break
            k += 1
    if base.gates["feasibility"] == "fail":
        profile, gi = inp.profile, inp.gate_inputs
        needed = math.ceil(
            gi.estimated_effort_hours / (profile.weeks_available * profile.team_size)
        )
        available = profile.hours_per_week * profile.weeks_available * profile.team_size
        candidates.append(
            WhatIfCandidate(
                dimension="feasibility_gate",
                field="hours_per_week",
                from_=profile.hours_per_week,
                to=needed,
                alt=f"or cut scope by {gi.estimated_effort_hours - available:g} h",
                delta=None,
                new_verdict="re-evaluate",
                stable=None,
            )
        )

    def order(c: WhatIfCandidate) -> tuple[float, bool, int]:
        return (
            c.delta if c.delta is not None else 99,
            not c.stable,
            DIMS.index(c.dimension) if c.dimension in DIMS else 9,
        )

    candidates.sort(key=order)
    return candidates[: cfg.whatif.max_candidates]


def evaluate_with_whatif(inp: EngineInput, cfg: EngineConfig) -> EvaluationResult:
    base = evaluate(inp, cfg)
    return base.model_copy(update={"what_would_make_go": suggest(inp, cfg, base)})


def value_of_information(
    inp: EngineInput, cfg: EngineConfig, unknown: Unknown, low: float, high: float
) -> VoiResponse:
    """Evaluate the verdict at both ends of the range; if it differs, a question is worth asking."""

    def point(value: float) -> VoiPoint:
        result = evaluate(apply_value(inp, unknown.scope, unknown.field, value), cfg)
        return VoiPoint(
            value=value, verdict=result.verdict, stability=result.stability, score=result.score
        )

    low_point, high_point = point(low), point(high)
    return VoiResponse(
        low=low_point, high=high_point, verdict_changes=low_point.verdict != high_point.verdict
    )
