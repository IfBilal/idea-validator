"""The stable raw item every connector produces (docs/harness/04 raw_pages, input of M6i).

``{source, url, title, text, created_at, score, platform, metadata}``. Text is data, never an
instruction: it is cleaned and length-capped here and never interpreted by this service.
"""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TITLE = 300
MAX_TEXT = 8000
MAX_URL = 2048
_TAG = re.compile(r"<[^>]{0,500}>")
_ACTIVE = re.compile(r"<(script|style)\b[^>]{0,500}>.{0,3000}?</\1\s*>", re.DOTALL | re.IGNORECASE)
_SPACE = re.compile(r"[ \t\u00a0]+")
_BLANKS = re.compile(r"\n{3,}")
# zero-width, bidi and other invisible format characters plus controls (except \n and \t)
_DROP_CATEGORIES = {"Cf", "Cc", "Cs", "Co"}


def clean_text(value: str, limit: int, *, strip_tags: bool = False) -> str:
    """Remove markup (optional), control and invisible characters, collapse blanks, cap length."""
    value = value[: limit * 4]  # bound the work on hostile input before anything else
    if strip_tags:
        value = _TAG.sub(" ", _ACTIVE.sub(" ", value))
        for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
            value = value.replace(entity, char)
        value = value.replace("&amp;", "&")
    value = "".join(
        ch for ch in value if ch in "\n\t" or unicodedata.category(ch) not in _DROP_CATEGORIES
    )
    value = _SPACE.sub(" ", value)
    value = "\n".join(line.strip() for line in value.split("\n"))
    value = _BLANKS.sub("\n\n", value).strip()
    return value[:limit]


class RawItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, max_length=40)
    url: str
    title: str
    text: str = ""
    created_at: datetime | None = None
    score: int | None = Field(default=None, ge=0)  # points / stars / votes
    platform: str = Field(min_length=1, max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip()
        parts = urlsplit(value)
        if len(value) > MAX_URL or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be an http(s) url")
        if parts.username or parts.password:
            raise ValueError("url must not carry credentials")
        return value

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        cleaned = clean_text(value, MAX_TITLE, strip_tags=True)
        if not cleaned:
            raise ValueError("title is empty")
        return cleaned.replace("\n", " ")

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return clean_text(value, MAX_TEXT, strip_tags=True)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @property
    def content_hash(self) -> str:
        """Hash of what the text says (not of volatile counters like score)."""
        material = "\x1f".join((self.url, self.title, self.text))
        return hashlib.sha256(material.encode()).hexdigest()

    def body(self) -> dict[str, Any]:
        """The JSON stored in ``raw_pages.body`` and read by ingestion."""
        return self.model_dump(mode="json")
