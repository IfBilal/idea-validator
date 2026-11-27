"""POST /objections/filter request and response (Skeptic objections in, evidence-backed out)."""

from pydantic import Field

from common.schemas import Message
from common.schemas.types import Dimension, EvidenceId


class Objection(Message):
    objection_id: str = Field(min_length=1, max_length=64)
    dimension: Dimension | None = None
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[EvidenceId] = Field(max_length=20)


class ObjectionFilterRequest(Message):
    run_id: str = Field(min_length=1, max_length=64)
    objections: list[Objection]


class AcceptedObjection(Message):
    objection_id: str
    dimension: Dimension | None
    text: str
    evidence_ids: list[EvidenceId]
    supporting_quote: str


class RejectedObjection(Message):
    objection_id: str
    reason: str


class ObjectionFilterResponse(Message):
    accepted: list[AcceptedObjection]
    rejected: list[RejectedObjection]
