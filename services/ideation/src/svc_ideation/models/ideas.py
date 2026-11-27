"""Idea, finding, objection and change models shared by the five M8 agents.

Requests and responses are ``Message`` subclasses (extra=forbid, frozen). The ``Raw*`` variants
parse model output: they ignore unknown keys (``verdict``, ``score``, ``decision`` ...), so
nothing a model adds beyond the schema can ever reach a response.
"""

import hashlib
import re
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from common.schemas.base import Message
from common.schemas.types import Dimension, EvidenceId

Scope = Literal["S", "M", "L"]
Severity = Literal["fatal", "major", "minor"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Ref = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
RunId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]

_EVIDENCE_ID = re.compile(r"^card_[0-9a-f]{8,12}#c\d+$")


def _clean_ids(value: Any) -> Any:
    """Keep only well-formed, unique evidence ids from a model-written list."""
    if not isinstance(value, list):
        return value
    return list(dict.fromkeys(v for v in value if isinstance(v, str) and _EVIDENCE_ID.match(v)))


def idea_ref_for(title: str) -> str:
    """Stable id for an idea: same title (case/space-insensitive) -> same ref."""
    normal = " ".join(title.lower().split())
    return "idea_" + hashlib.sha1(normal.encode(), usedforsecurity=False).hexdigest()[:10]


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode(), usedforsecurity=False).hexdigest()[:10]
    return f"{prefix}_{digest}"


class Finding(Message):
    """A complaint or missing feature, backed by evidence ids (Gap Analyst)."""

    text: Text
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=10)


class IdeaBody(Message):
    title: Title
    summary: Text
    target_user: Short
    core_features: list[Short] = Field(min_length=3, max_length=6)
    gap_ref: Ref
    gap_evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=20)
    fit_reason: Text
    est_scope: Scope

    @field_validator("est_scope", mode="before")
    @classmethod
    def _scope_case(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("gap_evidence_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> Any:
        return _clean_ids(value)

    @field_validator("core_features", mode="before")
    @classmethod
    def _unique_features(cls, value: Any) -> Any:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return value
        seen: dict[str, str] = {}
        for feature in value:
            seen.setdefault(" ".join(feature.lower().split()), feature)
        return list(seen.values())


class RawIdea(IdeaBody):
    """One idea as written by the Ideator model; unknown keys are discarded."""

    model_config = ConfigDict(extra="ignore")


class Idea(IdeaBody):
    idea_ref: Ref
    niche: Short | None = None
    complaints: list[Finding] = Field(default_factory=list, max_length=20)
    missing_features: list[Finding] = Field(default_factory=list, max_length=20)

    @classmethod
    def from_raw(cls, raw: RawIdea, niche: str | None = None) -> "Idea":
        return cls(**raw.model_dump(), idea_ref=idea_ref_for(raw.title), niche=niche)


class Objection(Message):
    objection_id: Ref
    idea_ref: Ref
    dimension: Dimension
    claim: Text
    severity: Severity
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=10)


class Change(Message):
    change_id: Ref
    text: Text
    target_dimension: Dimension
    rationale: str = Field(default="", max_length=1000)
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=10)


class _RawCited(Message):
    """Model output that cites evidence; unknown keys are discarded, malformed ids filtered."""

    model_config = ConfigDict(extra="ignore")

    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> Any:
        return _clean_ids(value)


class RawFinding(_RawCited):
    text: Text


class RawObjection(_RawCited):
    idea_ref: Ref
    dimension: Dimension
    claim: Text
    severity: Severity


class RawChange(_RawCited):
    text: Text
    rationale: str = Field(default="", max_length=1000)
