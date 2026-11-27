"""Base class for every inter-module message (CLAUDE.md rule 3, 21 §2)."""

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    """Immutable, strict message: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
