"""Pure helpers for the Validate graph: no I/O, no clock, no randomness.

They shape evidence, parse scorer output under the *evidence or abstain* rule, build the request
for M11 and decide which questions are worth asking. None of them decides a verdict.
"""

import math
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from common.schemas import DimensionScore, EvidenceItem
from common.schemas.types import DIMENSIONS

from ..models.api import EFFORT_CAP, FIELD_RANGES, GATE_FIELDS, PROFILE_FIELDS, UnknownSpec

MAX_QUESTIONS = 3
MIN_SOURCES = 3
MIN_PLATFORMS = 2
QUERY_CHARS = 300
CLAIM_TEXT_MAX = 600
REASON_MAX = 600


# ---- evidence ------------------------------------------------------------------------------
def evidence_queries(idea_text: str) -> list[str]:
    base = " ".join(idea_text.split())[:QUERY_CHARS]
    return [
        f"problem: {base}",
        f"competitors and alternatives to {base}",
        f"complaints and frustrations about {base}",
        f"demand: people looking for {base}",
    ]


def merge_evidence(batches: Iterable[Iterable[EvidenceItem]]) -> list[EvidenceItem]:
    seen: set[str] = set()
    merged: list[EvidenceItem] = []
    for batch in batches:
        for item in batch:
            if item.evidence_id not in seen:
                seen.add(item.evidence_id)
                merged.append(item)
    return merged


def evidence_stats(items: Iterable[EvidenceItem]) -> tuple[int, int]:
    """(distinct cards, distinct platforms): the inputs of the evidence gate."""
    listed = list(items)
    return len({i.card_id for i in listed}), len({i.source for i in listed})


def is_thin(n_sources: int, n_platforms: int) -> bool:
    return n_sources < MIN_SOURCES or n_platforms < MIN_PLATFORMS


def scorer_evidence(items: Iterable[EvidenceItem]) -> list[dict[str, str]]:
    return [{"evidence_id": i.evidence_id, "quote": i.quote} for i in items]


# ---- scores: evidence or abstain -----------------------------------------------------------
def _abstention(dimension: str, rubric_versions: Mapping[str, str], reason: str) -> DimensionScore:
    return DimensionScore(
        dimension=dimension,
        score=None,
        confidence=0.0,
        evidence_ids=[],
        reason=f"abstained: {reason}"[:REASON_MAX],
        rubric_version=rubric_versions.get(dimension, f"{dimension}@v1"),
        model="none",
        abstained=True,
    )


def abstain_all(rubric_versions: Mapping[str, str], reason: str) -> list[DimensionScore]:
    return [_abstention(d, rubric_versions, reason) for d in DIMENSIONS]


def parse_scores(
    raw: list[Any], allowed_evidence_ids: set[str], rubric_versions: Mapping[str, str]
) -> list[DimensionScore]:
    """Exactly six scores in canonical order. Anything doubtful becomes an abstention."""
    parsed: dict[str, DimensionScore] = {}
    for item in raw:
        try:
            score = DimensionScore.model_validate(item)
        except ValidationError:
            continue
        parsed.setdefault(score.dimension, score)
    out: list[DimensionScore] = []
    for dim in DIMENSIONS:
        found = parsed.get(dim)
        if found is None:
            out.append(_abstention(dim, rubric_versions, "no valid result from the scorer"))
            continue
        valid_ids = [e for e in found.evidence_ids if e in allowed_evidence_ids]
        if found.score is None:
            out.append(
                found.model_copy(update={"confidence": 0.0, "evidence_ids": [], "abstained": True})
            )
        elif not valid_ids:
            out.append(_abstention(dim, rubric_versions, "no valid evidence for the score"))
        else:
            out.append(found.model_copy(update={"evidence_ids": valid_ids}))
    return out


# ---- the M11 request -----------------------------------------------------------------------
def profile_for_verdict(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "goal": profile["goal"],
        "hours_per_week": profile["hours_per_week"],
        "weeks_available": profile["weeks_available"],
        "team_size": profile["team_size"],
    }


def _number(value: Any) -> float | None:
    """A usable effort: finite, non-bool, within 0..EFFORT_CAP. Anything else is unknown."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return value if 0 <= value <= EFFORT_CAP else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def effort_is_unknown(
    extra: Mapping[str, Any], overrides: Mapping[str, float], answers: Mapping[str, float | None]
) -> bool:
    return (
        answers.get("estimated_effort_hours") is None
        and "estimated_effort_hours" not in overrides
        and _number(extra.get("estimated_effort_hours")) is None
    )


def build_verdict_request(
    *,
    idea_id: str,
    version: int,
    run_id: str,
    profile: Mapping[str, Any],
    scores: list[DimensionScore],
    n_sources: int,
    n_platforms: int,
    extra: Mapping[str, Any],
    overrides: Mapping[str, float],
    answers: Mapping[str, float | None],
    objections: list[dict[str, str]],
) -> dict[str, Any]:
    """Body of ``POST M11 /verdict``. Precedence: scorer estimate < user override < user answer.

    Raises ``ValueError`` when the effort is unknown: a made-up number must never reach M11.
    """
    if effort_is_unknown(extra, overrides, answers):
        raise ValueError("estimated_effort_hours is unknown; ask the user instead of guessing")
    values: dict[str, float] = {}
    base_effort = _number(extra.get("estimated_effort_hours"))
    if base_effort is not None:
        values["estimated_effort_hours"] = base_effort
    values.update(overrides)
    values.update({k: v for k, v in answers.items() if v is not None})

    gate: dict[str, Any] = {
        "n_sources": n_sources,
        "n_platforms": n_platforms,
        "estimated_effort_hours": values["estimated_effort_hours"],
        "risk_blocker": _text(extra.get("risk_blocker")),
        "duplicate_of": _text(extra.get("duplicate_of")),
    }
    profile_out = dict(profile_for_verdict(profile))
    for field in PROFILE_FIELDS:
        if field in values:
            profile_out[field] = values[field]
    score_dicts = [s.model_dump(mode="json") for s in scores]
    return {
        "idea_id": idea_id,
        "version": version,
        "run_id": run_id,
        "profile": profile_out,
        "scores": score_dicts,
        "gate_inputs": gate,
        "objections_accepted": list(objections),
    }


# ---- clarifying questions ------------------------------------------------------------------
def pending_unknowns(
    extra: Mapping[str, Any],
    overrides: Mapping[str, float],
    declared: list[dict[str, Any]],
    asked: list[str],
    vetted: Iterable[str] = (),
) -> list[UnknownSpec]:
    """Unknowns still worth a VOI check, in priority order; at most ``MAX_QUESTIONS`` in total."""
    if len(asked) >= MAX_QUESTIONS:
        return []
    specs: list[UnknownSpec] = []
    effort = _number(extra.get("estimated_effort_hours"))
    if effort and "estimated_effort_hours" not in overrides:
        specs.append(
            UnknownSpec(
                scope="gate_input",
                field="estimated_effort_hours",
                range=(round(effort * 0.5), min(EFFORT_CAP, round(effort * 2))),
                question="About how many hours will building this take you?",
            )
        )
    specs.extend(UnknownSpec.model_validate(d) for d in declared)
    seen: set[str] = set(vetted)  # already checked: the answer could not change the verdict
    pending: list[UnknownSpec] = []
    for spec in specs:
        if spec.field in overrides or spec.field in asked or spec.field in seen:
            continue
        seen.add(spec.field)
        pending.append(spec)
    return pending[: MAX_QUESTIONS - len(asked)]


def effort_question() -> dict[str, Any]:
    """Mandatory question: M11 needs the build hours and the scorers gave no usable estimate."""
    return {
        "question_id": "q_estimated_effort_hours",
        "field": "estimated_effort_hours",
        "scope": "gate_input",
        "text": "About how many hours will building this take you?",
        "range": [0, EFFORT_CAP],
        "required": True,
        "why_it_matters": (
            "No verdict can be given without an effort estimate; "
            "the scorers could not provide one and we will not guess."
        ),
    }


def voi_request(verdict_request: dict[str, Any], spec: UnknownSpec) -> dict[str, Any]:
    return {
        "input": verdict_request,
        "unknown": {"scope": spec.scope, "field": spec.field},
        "range": [spec.range[0], spec.range[1]],
    }


def question_payload(
    spec: UnknownSpec, *, low: tuple[str, float], high: tuple[str, float]
) -> dict[str, Any]:
    return {
        "question_id": f"q_{spec.field}",
        "field": spec.field,
        "scope": spec.scope,
        "required": False,
        "text": spec.question or f"What is the value of {spec.field}?",
        "range": list(FIELD_RANGES[spec.field]),  # what the user may answer, not the VOI probe
        "why_it_matters": (
            f"The verdict is {low[0]} if {spec.field} is {low[1]:g} "
            f"but {high[0]} if it is {high[1]:g}."
        ),
    }


# ---- claims --------------------------------------------------------------------------------
def build_claims(scores: list[DimensionScore]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for s in scores:
        if s.score is None or not s.evidence_ids:
            continue
        claims.append(
            {
                "claim_id": f"cl_{s.dimension}",
                "text": (s.reason or f"{s.dimension} score {s.score}")[:CLAIM_TEXT_MAX],
                "evidence_ids": list(s.evidence_ids),
                "dimension": s.dimension,
            }
        )
    return claims


def apply_claim_results(
    scores: list[DimensionScore], results: list[dict[str, Any]]
) -> tuple[list[DimensionScore], list[str]]:
    """Unsupported (or unchecked) claims are removed: their dimension abstains. Fail closed."""
    supported = {r["claim_id"]: bool(r.get("supported")) for r in results}
    removed: list[str] = []
    out: list[DimensionScore] = []
    for s in scores:
        claim_id = f"cl_{s.dimension}"
        if s.score is not None and s.evidence_ids and not supported.get(claim_id, False):
            removed.append(claim_id)
            out.append(
                s.model_copy(
                    update={
                        "score": None,
                        "confidence": 0.0,
                        "evidence_ids": [],
                        "abstained": True,
                        "reason": "abstained: claim not supported by its evidence",
                    }
                )
            )
        else:
            out.append(s)
    return out, removed


__all__ = ["GATE_FIELDS", "MAX_QUESTIONS", "UnknownSpec"]


# ---- suggested changes and the what-if diff----------------------------------------------------
MAX_CHANGES = 3


def _score_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def change_candidates(what_would_make_go: list[Any]) -> list[dict[str, Any]]:
    """M11 dimension deltas that are usable for M8 ``/suggest-changes`` (at most three)."""
    out: list[dict[str, Any]] = []
    for entry in what_would_make_go:
        if not isinstance(entry, Mapping) or entry.get("dimension") not in DIMENSIONS:
            continue
        frm, to = _score_number(entry.get("from")), _score_number(entry.get("to"))
        if frm is None or to is None or not 0 <= frm < to <= 5:
            continue
        out.append(
            {
                "dimension": entry["dimension"],
                "from": frm,
                "to": to,
                "new_verdict": entry.get("new_verdict"),
            }
        )
    return out[:MAX_CHANGES]


def build_diff(base: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Plain comparison of two cards; it only reads M11's outputs."""
    before = {s["dimension"]: s.get("score") for s in base.get("scores") or []}
    after = {s["dimension"]: s.get("score") for s in new.get("scores") or []}
    rows: list[dict[str, Any]] = []
    for dim in DIMENSIONS:
        b, a = _score_number(before.get(dim)), _score_number(after.get(dim))
        if b is not None and a is not None:
            rows.append({"dimension": dim, "from": b, "to": a, "delta": round(a - b, 6)})
    return {
        "verdict_from": base.get("verdict"),
        "verdict_to": new.get("verdict"),
        "verdict_changed": base.get("verdict") != new.get("verdict"),
        "score_from": base.get("score"),
        "score_to": new.get("score"),
        "dimensions": rows,
    }
