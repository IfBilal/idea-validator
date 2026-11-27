"""Example schema validator (docs/harness/11 §1.5, 09 §7).

One JSON object per line: ``{"messages": [system, user, assistant], "meta": {...}}``.
Rules enforced here (each is a hard project rule, not style):

* the assistant target is exactly four keys (score, confidence, evidence_ids, reason);
* ``score`` is null (abstain) or in 0..5 on a 0.5 grid;
* cited evidence ids are a subset of the ids shown in that example's own EVIDENCE block;
* abstain  <=>  score null, confidence 0, no ids;  a prompt with no evidence must abstain;
* ``hand_checked`` carries its audit trail (who, when, original and final label).
"""

import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from common.schemas import DIMENSIONS

EVIDENCE_ID = re.compile(r"^card_[0-9a-f]{8,12}#c\d+$")
_PROMPT_IDS = re.compile(r'<evidence id="([^"]+)">')
_VERDICT_WORDS = re.compile(r"(?i:\bverdict\b)|\b(?:GO|NO-GO|PIVOT|INSUFFICIENT[_ ]EVIDENCE)\b")
TARGET_KEYS = {"score", "confidence", "evidence_ids", "reason"}


class ExampleError(ValueError):
    """The example violates the dataset contract."""


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")
    idea_id: str
    dimension: str
    level: float | None
    hand_checked: bool
    rubric_version: str
    evidence_ids: list[str] = []
    synthetic: bool = False
    checked_by: str | None = None
    checked_at: str | None = None
    original_label: float | None = None
    final_label: float | None = None


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[Message]
    meta: Meta


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float | None
    confidence: float
    evidence_ids: list[str]
    reason: str


def on_grid(score: float) -> bool:
    return math.isfinite(score) and 0 <= score <= 5 and abs(score * 2 - round(score * 2)) < 1e-9


def prompt_evidence_ids(user_text: str) -> list[str]:
    return _PROMPT_IDS.findall(user_text)


def parse_target(text: str) -> Target:
    try:
        raw: Any = json.loads(text)
        target = Target.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ExampleError(f"assistant target is not the score object: {exc}") from exc
    return target


def validate_example(raw: dict[str, Any]) -> Example:
    try:
        ex = Example.model_validate(raw)
    except ValidationError as exc:
        raise ExampleError(f"bad example shape: {exc}") from exc
    if [m.role for m in ex.messages] != ["system", "user", "assistant"]:
        raise ExampleError("messages must be exactly system, user, assistant")
    meta = ex.meta
    if meta.dimension not in DIMENSIONS:
        raise ExampleError(f"unknown dimension {meta.dimension!r}")
    target = parse_target(ex.messages[2].content)
    if _VERDICT_WORDS.search(target.reason):
        raise ExampleError(
            "verdict wording in the target reason (the model never decides a verdict)"
        )
    if not (math.isfinite(target.confidence) and 0 <= target.confidence <= 1):
        raise ExampleError("confidence must be within [0, 1]")
    shown = prompt_evidence_ids(ex.messages[1].content)
    if target.score is None:
        if target.confidence != 0 or target.evidence_ids:
            raise ExampleError("abstain means score null, confidence 0 and no evidence ids")
    else:
        if not on_grid(target.score):
            raise ExampleError(f"score {target.score} is not on the 0.5 grid within 0..5")
        if not shown:
            raise ExampleError("evidence block is empty: the target must abstain")
        if not target.evidence_ids:
            raise ExampleError("a scored example must cite at least one evidence id")
        for ident in target.evidence_ids:
            if not EVIDENCE_ID.match(ident) or ident not in shown:
                raise ExampleError(f"cited id {ident!r} is not in the evidence block")
    if meta.hand_checked:
        for field in ("checked_by", "checked_at"):
            if not getattr(meta, field):
                raise ExampleError(f"hand_checked requires {field}")
        if meta.final_label is None and target.score is not None:
            raise ExampleError("hand_checked requires final_label")
    return ex


def load_jsonl(path: str) -> list[Example]:
    out: list[Example] = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                out.append(validate_example(json.loads(line)))
            except (ExampleError, json.JSONDecodeError) as exc:
                raise ExampleError(f"{path}:{number}: {exc}") from exc
    return out
