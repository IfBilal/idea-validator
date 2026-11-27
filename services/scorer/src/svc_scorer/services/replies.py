"""Validate the model's JSON reply. Pure functions: no I/O, no framework.

Unknown keys (for example a stray ``verdict``) are dropped, never propagated: the verdict is
computed only by M11 (CLAUDE.md rule 1).
"""

import json
import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

REASON_MAX = 600
_FENCE = re.compile(r"^```[a-zA-Z0-9]*\s*(.*?)\s*```$", re.DOTALL)


class ReplyError(ValueError):
    """The model reply is not a valid score object."""


@dataclass(frozen=True)
class ParsedReply:
    score: float | None
    confidence: float
    evidence_ids: list[str]
    reason: str


def _reject_constant(name: str) -> Any:
    raise ReplyError(f"non-finite number {name} in reply")


def _number(value: Any, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReplyError(f"{field} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ReplyError(f"{field} must be within [{low}, {high}]")
    return float(value)


def parse_reply(text: str) -> ParsedReply:
    stripped = text.strip()
    if fenced := _FENCE.match(stripped):
        stripped = fenced.group(1)
    if not stripped:
        raise ReplyError("empty reply")
    try:
        body = json.loads(stripped, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ReplyError("reply is not valid JSON") from exc
    if not isinstance(body, dict):
        raise ReplyError("reply must be a JSON object")
    for key in ("score", "confidence", "evidence_ids", "reason"):
        if key not in body:
            raise ReplyError(f"missing field {key}")

    score = None if body["score"] is None else _number(body["score"], "score", 0, 5)
    confidence = _number(body["confidence"], "confidence", 0, 1)
    ids = body["evidence_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ReplyError("evidence_ids must be a list of strings")
    reason = body["reason"]
    if not isinstance(reason, str):
        raise ReplyError("reason must be a string")
    return ParsedReply(
        score=score,
        confidence=0.0 if score is None else confidence,
        evidence_ids=list(dict.fromkeys(ids)),
        reason=reason[:REASON_MAX],
    )


def resolve_evidence(cited: list[str], provided: Collection[str]) -> list[str]:
    """Keep only ids that were in the request (CLAUDE.md rule 2); order preserved."""
    return [i for i in cited if i in provided]
