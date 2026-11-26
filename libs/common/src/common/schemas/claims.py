from pydantic import Field

from .base import Message
from .types import Dimension, EvidenceId


class Claim(Message):
    claim_id: str
    text: str = Field(max_length=600)
    evidence_ids: list[EvidenceId]
    dimension: Dimension | None = None


class ClaimCheckRequest(Message):
    run_id: str
    claims: list[Claim]


class ClaimResult(Message):
    claim_id: str
    supported: bool
    reason: str
    supporting_quote: str | None = None
    checked_evidence_ids: list[str] | None = None


class ClaimCheckResponse(Message):
    results: list[ClaimResult]
