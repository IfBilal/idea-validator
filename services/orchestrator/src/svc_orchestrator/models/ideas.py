"""Views of M8's idea, finding, objection and change objects (docs/harness/05, M8 section).

M3 cannot import M8, so these mirror only what the orchestrator reads. They ignore unknown keys and
validate everything they accept, so a misbehaving upstream cannot smuggle fields into a card.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from common.schemas.types import Dimension, EvidenceId

Severity = Literal["fatal", "major", "minor"]
# Model-written refs end up in thread ids, cache keys and objection ids: keep them boring.
SafeRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Scope = Literal["S", "M", "L"]


class _View(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Finding(_View):
    text: str
    evidence_ids: list[EvidenceId] = Field(default_factory=list)


class Idea(_View):
    idea_ref: SafeRef
    title: str
    summary: str
    target_user: str
    core_features: list[str] = Field(min_length=1)
    gap_ref: str
    gap_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    fit_reason: str
    est_scope: Scope
    niche: str | None = None
    complaints: list[Finding] = Field(default_factory=list)
    missing_features: list[Finding] = Field(default_factory=list)


class Objection(_View):
    objection_id: SafeRef
    idea_ref: SafeRef
    dimension: Dimension
    claim: str
    severity: Severity
    evidence_ids: list[EvidenceId] = Field(min_length=1)


class Change(_View):
    change_id: str
    text: str
    target_dimension: Dimension
    rationale: str = ""
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
