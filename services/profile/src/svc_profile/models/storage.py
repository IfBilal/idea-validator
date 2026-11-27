"""Request and response models for idea/version/verdict/run/approval/cache storage."""

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

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


MAX_CARD_BYTES = 256 * 1024


class PutVersion(Message):
    """PUT /internal/ideas/{id}/versions/{v}: a new version with its card, stored verbatim."""

    user_id: uuid.UUID
    text: str = Field(min_length=1, max_length=4000)
    change_note: str | None = Field(default=None, max_length=500)
    card: dict[str, Any]
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    base_version: int = Field(ge=1)

    @field_validator("card")
    @classmethod
    def _card_is_small_json(cls, card: dict[str, Any]) -> dict[str, Any]:
        try:
            size = len(json.dumps(card, allow_nan=False, ensure_ascii=False).encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("card must be plain JSON") from exc
        if size > MAX_CARD_BYTES:
            raise ValueError(f"card exceeds {MAX_CARD_BYTES} bytes")
        return card


class VersionPut(Message):
    idea_id: str
    version: int
    created: bool


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
    card: dict[str, Any] | None
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
