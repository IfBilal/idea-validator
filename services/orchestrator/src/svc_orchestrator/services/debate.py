"""Pure rules of the Discover debate (docs/harness/06 §1, 07 §3). No I/O, no clock, no LLM.

Nothing here decides or alters a verdict: ranking only reads what M11 returned.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..models.ideas import Idea, Objection

MAX_ROUNDS = 3  # hard cap: config can lower it, never raise it
M8_MAX_IDEAS = 20
OVER_GENERATE = 3  # ask for count + 3: some ideas get dropped
IDEA_TEXT_MAX = 4000
SCOPE_HOURS = {"S": 60, "M": 160, "L": 400}  # same table M8 uses to check the student's hours
VERDICT_RANK = {"GO": 4, "PIVOT": 3, "INSUFFICIENT_EVIDENCE": 2, "NO-GO": 1}


def _dropped(idea: Idea, round_: int, reason: str, evidence_ids: Iterable[str]) -> dict[str, Any]:
    return {
        "idea_ref": idea.idea_ref,
        "title": idea.title,
        "round": round_,
        "reason": reason,
        "defeated_by_evidence_ids": sorted(set(evidence_ids)),
    }


def require_gap_evidence(
    ideas: list[Idea], *, round_: int
) -> tuple[list[Idea], list[dict[str, Any]]]:
    """Drop ideas the Gap Analyst could not back with a complaint or a missing feature."""
    kept: list[Idea] = []
    dropped: list[dict[str, Any]] = []
    for idea in ideas:
        if idea.gap_evidence_ids and (idea.complaints or idea.missing_features):
            kept.append(idea)
        else:
            dropped.append(_dropped(idea, round_, "no_gap_evidence", []))
    return kept, dropped


@dataclass(frozen=True)
class DebateOutcome:
    survivors: list[Idea]
    dropped: list[dict[str, Any]]
    nonfatal: list[Objection]  # accepted, non-fatal objections on surviving ideas


def apply_objections(ideas: list[Idea], accepted: list[Objection], *, round_: int) -> DebateOutcome:
    """An idea is defeated by one accepted fatal objection or by two on different dimensions."""
    survivors: list[Idea] = []
    dropped: list[dict[str, Any]] = []
    nonfatal: list[Objection] = []
    for idea in ideas:
        mine = [o for o in accepted if o.idea_ref == idea.idea_ref]
        evidence = [e for o in mine for e in o.evidence_ids]
        if any(o.severity == "fatal" for o in mine):
            dropped.append(_dropped(idea, round_, "fatal_objection", evidence))
        elif len({o.dimension for o in mine}) >= 2:
            dropped.append(_dropped(idea, round_, "multiple_objections", evidence))
        else:
            survivors.append(idea)
            nonfatal.extend(mine)
    return DebateOutcome(survivors, dropped, nonfatal)


def unseen(objections: list[Objection], seen: set[str]) -> list[Objection]:
    return [o for o in objections if o.objection_id not in seen]


def should_continue(
    round_: int, max_rounds: int, *, new_nonfatal: int, survivors: int, requested: int
) -> bool:
    """Another round: under the cap, new non-fatal objections, and too few survivors."""
    cap = min(max_rounds, MAX_ROUNDS)
    return round_ < cap and new_nonfatal > 0 and survivors < requested


def ideation_count(round_: int, requested: int, survivors: int) -> int:
    """Over-generate by 3; in later rounds only ask for what is still missing."""
    return min(M8_MAX_IDEAS, max(1, requested + OVER_GENERATE - survivors))


def verdict_rank(verdict: str | None) -> int:
    return VERDICT_RANK.get(verdict or "", 0)


def rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verdict rank, then score, then confidence (A-19). Stable; reads M11's output only."""

    def key(item: dict[str, Any]) -> tuple[int, float, float]:
        score, conf = item.get("score"), item.get("confidence")
        return (
            verdict_rank(item.get("verdict")),
            float(score) if score is not None else -1.0,
            float(conf) if conf is not None else -1.0,
        )

    return sorted(items, key=key, reverse=True)


def scope_hours(scope: str) -> int:
    if scope not in SCOPE_HOURS:
        raise ValueError(f"unknown scope {scope!r}")
    return SCOPE_HOURS[scope]


def idea_text(idea: Idea) -> str:
    text = (
        f"{idea.title}. {idea.summary} Target user: {idea.target_user}. "
        f"Core features: {'; '.join(idea.core_features)}."
    )
    return text[:IDEA_TEXT_MAX]


def m11_objections(accepted: list[Objection]) -> list[dict[str, str]]:
    return [{"dimension": o.dimension, "objection_id": o.objection_id} for o in accepted]


def result_item(idea: Idea, card: dict[str, Any], version: int) -> dict[str, Any]:
    reasons = card.get("reasons") or []
    reason = (
        reasons[0] if reasons else card.get("stop_reason") or card.get("error") or idea.fit_reason
    )
    return {
        "idea_id": idea.idea_ref,
        "version": version,
        "title": idea.title,
        "summary": idea.summary,
        "niche": idea.niche,
        "verdict": card.get("verdict"),
        "stability": card.get("stability"),
        "score": card.get("score"),
        "confidence": card.get("confidence"),
        "reason": reason,
        "gap_evidence_ids": list(idea.gap_evidence_ids),
        "partial": bool(card.get("partial")),
        "needs_input": list(card.get("needs_input") or []),
    }
