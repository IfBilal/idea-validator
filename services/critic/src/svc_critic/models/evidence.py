"""An evidence chunk as returned by M6 ``POST /evidence/batch`` (unknown extra fields ignored)."""

from pydantic import BaseModel, ConfigDict

from common.schemas.types import CardId, EvidenceId


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    evidence_id: EvidenceId
    card_id: CardId | None = None
    card_name: str | None = None
    text: str | None = None
    quote: str | None = None

    @property
    def body(self) -> str:
        """Full chunk text, falling back to the short quote when M6 sends only that."""
        return self.text or self.quote or ""
