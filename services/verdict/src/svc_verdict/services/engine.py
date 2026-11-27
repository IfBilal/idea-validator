"""M11 Verdict Engine core (docs/harness/08). Plain Python.

No LLM, no network, no database, no randomness, no clock, no global state: the same input always
gives the same output. The language model never decides the verdict; this module does.
"""

import math
from dataclasses import dataclass
from typing import Final

from common.schemas.types import DIMENSIONS

from ..models.inputs import EngineInput
from ..models.results import EvaluationResult, Flip, SensitivityResult
from .engine_config import EngineConfig

DIMS: Final[tuple[str, ...]] = DIMENSIONS
FLOOR_DIMS: Final[tuple[str, ...]] = DIMS[:5]  # the floor does not apply to learning
EPS: Final = 1e-9
RANK: Final = {"GO": 4, "PIVOT": 3, "INSUFFICIENT_EVIDENCE": 2, "NO-GO": 1}


def round_to(value: float, digits: int = 4) -> float:
    return round(value + 0.0, digits)


def weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Sum of weight x score scaled to 0..100 (weights add to 100, scores are 0..5)."""
    return sum(weights[d] * scores[d] for d in DIMS) / 5.0


def shift_weights(weights: dict[str, float], dim: str, factor: float) -> dict[str, float]:
    """Move one weight by ``factor`` and share the difference among the others in proportion."""
    shifted = dict(weights)
    shifted[dim] = weights[dim] * (1 + factor)
    diff = shifted[dim] - weights[dim]
    rest = sum(weights[d] for d in DIMS if d != dim)
    for d in DIMS:
        if d != dim:
            shifted[d] = weights[d] - diff * (weights[d] / rest)
    return shifted


def overall_confidence(confidences: list[float], n_sources: int, cfg: EngineConfig) -> float:
    c = cfg.confidence
    mean = sum(confidences) / len(confidences)
    return c.w_dims * mean + c.w_sources * min(1.0, n_sources / c.sources_full)


def weakest_dimension(scores: dict[str, float], weights: dict[str, float], floor: float) -> str:
    """Lowest score among floor breaches (else all six); tie: larger weight, then fixed order."""
    breached = [d for d in FLOOR_DIMS if scores[d] < floor - EPS]
    pool = breached or list(DIMS)
    return sorted(pool, key=lambda d: (scores[d], -weights[d], DIMS.index(d)))[0]


@dataclass(frozen=True)
class CoreResult:
    verdict: str
    gates: dict[str, str]
    score: float | None
    confidence: float | None
    weakest: str | None
    reasons: list[str]
    weights: dict[str, float]
    adjusted_scores: dict[str, float | None]
    adjusted_confidences: dict[str, float]


def adjust_for_challenges(
    inp: EngineInput, cfg: EngineConfig
) -> tuple[dict[str, float | None], dict[str, float], list[str]]:
    """Step 1: each objection the critic accepted lowers score and confidence (capped)."""
    counts: dict[str, int] = {}
    for objection in inp.objections_accepted:
        counts[objection.dimension] = counts.get(objection.dimension, 0) + 1
    ch = cfg.challenge
    scores: dict[str, float | None] = {}
    confs: dict[str, float] = {}
    abstained: list[str] = []
    for s in inp.scores:
        n = counts.get(s.dimension, 0)
        if s.score is None:
            abstained.append(s.dimension)
            scores[s.dimension], confs[s.dimension] = None, 0.0
            continue
        scores[s.dimension] = max(0.0, min(5.0, s.score - min(ch.score_step * n, ch.score_max)))
        confs[s.dimension] = max(0.0, min(1.0, s.confidence - ch.conf_step * n))
    return scores, confs, abstained


def run_core(
    inp: EngineInput, cfg: EngineConfig, weights: dict[str, float] | None = None
) -> CoreResult:
    """Steps 1-6: challenge, gates, abstention, score, confidence, classification."""
    th = cfg.thresholds
    gc = cfg.gates
    profile, gi = inp.profile, inp.gate_inputs
    if weights is None:
        weights = cfg.weights_for(profile.goal)
    elif not math.isclose(sum(weights.values()), 100.0, abs_tol=1e-6):
        raise ValueError("weights must add to 100")
    scores, confs, abstained = adjust_for_challenges(inp, cfg)
    gates = {
        "evidence": "not_evaluated",
        "feasibility": "not_evaluated",
        "risk": "not_evaluated",
        "duplicate": "not_evaluated",
    }
    reasons: list[str] = []
    score: float | None = None
    confidence: float | None = None
    weakest: str | None = None

    def result(verdict: str, reason: str) -> CoreResult:
        return CoreResult(
            verdict, gates, score, confidence, weakest, [*reasons, reason], weights, scores, confs
        )

    # 2. hard gates in order; the first failing gate decides
    if gi.n_sources < gc.min_sources or gi.n_platforms < gc.min_platforms:
        gates["evidence"] = "fail"
        return result(
            "INSUFFICIENT_EVIDENCE",
            f"evidence gate: {gi.n_sources} of {gc.min_sources} independent sources, "
            f"{gi.n_platforms} of {gc.min_platforms} platforms",
        )
    gates["evidence"] = "pass"

    available = profile.hours_per_week * profile.weeks_available * profile.team_size
    if gi.estimated_effort_hours > available + EPS:
        gates["feasibility"] = "fail"
        gap = gi.estimated_effort_hours - available
        return result(
            "NO-GO",
            f"feasibility gate: needs ~{gi.estimated_effort_hours:g} h, "
            f"available {available:g} h (gap {gap:g} h)",
        )
    gates["feasibility"] = "pass"

    if gi.risk_blocker:
        gates["risk"] = "fail"
        return result("NO-GO", f"risk gate: blocker - {gi.risk_blocker}")
    gates["risk"] = "pass"

    diff = scores.get("differentiation")
    overridden = diff is not None and diff >= gc.duplicate_diff_override - EPS
    if gi.duplicate_of and not overridden:
        gates["duplicate"] = "fail"
        return result(
            "NO-GO",
            f"duplicate gate: existing product {gi.duplicate_of} "
            "has the same core features for the same user",
        )
    gates["duplicate"] = "pass"

    # 3. abstention: a scorer without evidence abstains and we do not guess
    if abstained:
        return result("INSUFFICIENT_EVIDENCE", "abstained (no evidence): " + ", ".join(abstained))

    # 4. weighted score (no dimension is None past this point)
    final = {d: float(v) for d, v in scores.items() if v is not None}
    total = weighted_score(final, weights)
    score = round_to(total)

    # 5. confidence
    conf_value = overall_confidence([confs[d] for d in DIMS], gi.n_sources, cfg)
    confidence = round_to(conf_value, 3)
    if conf_value < cfg.confidence.min - EPS:
        low = ", ".join(d for d in DIMS if confs[d] < 0.5) or "sources"
        return result(
            "INSUFFICIENT_EVIDENCE",
            f"confidence {conf_value:.2f} < {cfg.confidence.min:g}; low: {low}",
        )

    # 6. thresholds and floor
    floor_ok = all(final[d] >= th.floor - EPS for d in FLOOR_DIMS)
    if total >= th.T_go - EPS and floor_ok:
        return result(
            "GO", f"score {total:.2f} >= T_go {th.T_go:g} and no dimension below floor {th.floor:g}"
        )
    weakest = weakest_dimension(final, weights, th.floor)
    if total >= th.T_no - EPS:
        why = "floor breach" if not floor_ok else f"score {total:.2f} between T_no and T_go"
        return result("PIVOT", f"{why}; weakest dimension: {weakest}")
    return result("NO-GO", f"score {total:.2f} < T_no {th.T_no:g}")


def run_sensitivity(
    inp: EngineInput, cfg: EngineConfig, base_verdict: str, base: CoreResult
) -> SensitivityResult:
    """14 runs: each of the six weights +/-20% (shared proportionally) and the least-confident
    score +/-1. A run flips when its verdict differs from the base verdict."""
    weights = base.weights
    runs = 0
    flips: list[Flip] = []
    delta_w = cfg.sensitivity.weight_delta
    for d in DIMS:
        for factor in (+delta_w, -delta_w):
            r = run_core(inp, cfg, shift_weights(weights, d, factor))
            runs += 1
            if r.verdict != base_verdict:
                sign = "+" if factor > 0 else "-"
                flips.append(
                    Flip(
                        change=f"weight {d} {sign}{int(abs(factor) * 100)}%",
                        verdict=r.verdict,
                        score=r.score,
                    )
                )
    scored = [s for s in inp.scores if s.score is not None]
    if scored:
        # least confident after challenge adjustments; tie -> fixed dimension order
        least = sorted(
            scored, key=lambda s: (base.adjusted_confidences[s.dimension], DIMS.index(s.dimension))
        )[0]
        for delta in (+cfg.sensitivity.score_delta, -cfg.sensitivity.score_delta):
            changed = [
                s.model_copy(update={"score": max(0.0, min(5.0, s.score + delta))})
                if s.dimension == least.dimension and s.score is not None
                else s
                for s in inp.scores
            ]
            r = run_core(inp.model_copy(update={"scores": changed}), cfg)
            runs += 1
            if r.verdict != base_verdict:
                sign = "+" if delta > 0 else ""
                flips.append(
                    Flip(
                        change=f"score {least.dimension} {sign}{delta:g}",
                        verdict=r.verdict,
                        score=r.score,
                    )
                )
    return SensitivityResult(runs=runs, flipped=len(flips), flips=flips)


def evaluate(inp: EngineInput, cfg: EngineConfig) -> EvaluationResult:
    """Full evaluation for one input: verdict, stability, sensitivity (no what-if yet)."""
    core = run_core(inp, cfg)
    decided_by_score = core.score is not None and core.verdict != "INSUFFICIENT_EVIDENCE"
    if decided_by_score:
        sensitivity = run_sensitivity(inp, cfg, core.verdict, core)
    else:
        sensitivity = SensitivityResult(
            runs=0,
            flipped=0,
            flips=[],
            note="decided before scoring (gate / abstention / confidence)",
        )
    return EvaluationResult(
        idea_id=inp.idea_id,
        version=inp.version,
        run_id=inp.run_id,
        verdict=core.verdict,
        stability="BORDERLINE" if sensitivity.flipped else "STABLE",
        score=core.score,
        confidence=core.confidence,
        gates=core.gates,
        weights={k: round_to(v, 4) for k, v in core.weights.items()},
        thresholds=cfg.thresholds.model_dump(),
        weakest_dimension=core.weakest,
        reasons=core.reasons,
        adjusted_scores=core.adjusted_scores,
        sensitivity=sensitivity,
        versions=cfg.versions,
    )
