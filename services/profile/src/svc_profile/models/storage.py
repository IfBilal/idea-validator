"""Request and response models for idea/version/verdict/run/approval/cache storage."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from common.schemas import Verdict
from common.schemas.base import Message

RunKind = Literal["discover", "validate", "whatif", "challenge", "eval"]


class NewIdea(Message):
    user_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    source: Literal["own", "generated"] = "own"
    text: str = Field(min_length=1, max_length=4000)
    niche_id: str | None = None
    origin_run_id: str | None = None


class NewVersion(Message):
    user_id: uuid.UUID
    text: str = Field(min_length=1, max_length=4000)
    change_note: str | None = Field(default=None, max_length=500)
    parent_version: int | None = Field(default=None, ge=1)
    clarifications: dict[str, Any] | None = None


class IdeaCreated(Message):
    idea_id: str
    version: int


class VersionSummary(Message):
    version: int
    text: str
    change_note: str | None
    parent_version: int | None
    created_at: datetime
    verdict: str | None
    score: float | None
    stability: str | None


class VersionDetail(VersionSummary):
    idea_id: str
    title: str
    clarifications: dict[str, Any] | None
    verdict_payload: dict[str, Any] | None


class IdeaSummary(Message):
    idea_id: str
    title: str
    source: str
    latest_version: int
    latest_verdict: str | None
    score: float | None
    updated_at: datetime


class IdeaPage(Message):
    items: list[IdeaSummary]
    next_cursor: str | None


class StoreVerdict(Message):
    user_id: uuid.UUID
    idea_id: uuid.UUID
    version: int = Field(ge=1)
    profile_version: int = Field(ge=1)
    verdict: Verdict


class VerdictStored(Message):
    verdict_id: str


class RunBody(Message):
    user_id: uuid.UUID
    kind: RunKind
    state: str = Field(min_length=1, max_length=40)
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    pending_question: dict[str, Any] | None = None


class RunOut(RunBody):
    run_id: str
    created_at: datetime
    updated_at: datetime


class NewApproval(Message):
    run_id: str
    action: str = Field(min_length=1, max_length=60)
    estimated_cost_usd: float = Field(ge=0)
    budget_usd: float = Field(ge=0)
    spent_usd: float = Field(default=0, ge=0)


class ApprovalDecision(Message):
    decision: Literal["approved", "declined", "expired"]
    decided_by: uuid.UUID | None = None


class ApprovalOut(Message):
    id: str
    run_id: str
    action: str
    decision: str
    estimated_cost_usd: float | None
    budget_usd: float | None
    spent_usd: float | None
    requested_at: datetime
    decided_at: datetime | None


class CompareRow(Message):
    idea_id: str
    title: str
    version: int
    verdict: str | None
    stability: str | None
    score: float | None
    confidence: float | None
    scores: dict[str, float | None]


class CacheValue(Message):
    value: dict[str, Any]
    ttl_s: int | None = Field(default=None, ge=1, le=7 * 86400)


class CacheResult(Message):
    hit: bool
    value: dict[str, Any] | None = None
