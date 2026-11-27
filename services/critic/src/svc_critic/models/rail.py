"""POST /rail/input request and response."""

from typing import Literal

from pydantic import Field

from common.schemas import Message

RailKind = Literal["idea", "answer", "niche"]
MAX_PAYLOAD_CHARS = 32_000  # hard cap at the schema level; per-kind limits are policy


class RailRequest(Message):
    text: str = Field(max_length=MAX_PAYLOAD_CHARS)
    kind: RailKind


class RailResponse(Message):
    allowed: bool
    reasons: list[str]
    sanitized_text: str
