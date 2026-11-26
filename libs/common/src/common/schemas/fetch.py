from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field

from .base import Message

Uri = Annotated[str, Field(pattern=r"^https://", json_schema_extra={"format": "uri"})]


class FetchRequest(Message):
    url: Uri
    source: str
    run_id: str | None = None
    purpose: Literal["scout", "evidence", "model_import"]
    max_bytes: int | None = Field(default=None, le=5000000)


class SanitiserReport(Message):
    removed_patterns: int
    flagged: bool


class FetchResponse(Message):
    url: str
    final_url: str | None = None
    status: int
    content_type: str | None = None
    text: str | None = None
    json_: Any = Field(default=None, alias="json")
    cache_hit: bool
    sanitiser: SanitiserReport
    fetched_at: AwareDatetime
