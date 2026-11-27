"""The stable raw item every connector produces (docs/harness/04 raw_pages, input of M6i).

``{source, url, title, text, created_at, score, platform, metadata}``. Text is data, never an
instruction: it is cleaned and length-capped here and never interpreted by this service.
"""

import contextlib
import hashlib
import ipaddress
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TITLE = 300
MAX_TEXT = 8000
MAX_URL = 2048
_TAG = re.compile(r"<(?:/?[A-Za-z!?][^>]{0,500}|\|[^>]{0,200})>")
_ACTIVE = re.compile(r"<(script|style)\b[^>]{0,500}>.{0,3000}?</\1\s*>", re.DOTALL | re.IGNORECASE)
_SPACE = re.compile(r"[ \t\u00a0]+")
_BLANKS = re.compile(r"\n{3,}")
# zero-width, bidi and other invisible format characters plus controls (except \n and \t)
_DROP_CATEGORIES = {"Cf", "Cc", "Cs", "Co"}


_IMAGE = re.compile(r"!\[[^\]]{0,300}\]\([^)]{0,2000}\)")
_OPEN_TAG = re.compile(r"<(?=[/A-Za-z|!\[])")
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}")
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".lan", ".home", ".corp")
MISSING = object()


def clean_text(value: str, limit: int, *, strip_tags: bool = False) -> str:
    """Remove markup (optional), control and invisible characters, collapse blanks, cap length.

    HTML entities are deliberately NOT decoded: M5 sanitised this text already, and decoding
    afterwards would recreate ``</evidence>``-style markup out of ``&lt;/evidence&gt;``.
    """
    value = value[: limit * 4]  # bound the work on hostile input before anything else
    # invisible characters first, so "<\u200bscript>" cannot hide from the tag stripper
    value = "".join(
        ch for ch in value if ch in "\n\t" or unicodedata.category(ch) not in _DROP_CATEGORIES
    )
    if strip_tags:
        value = _TAG.sub(" ", _ACTIVE.sub(" ", value))
    value = _IMAGE.sub("[removed: image link]", value)  # markdown image exfiltration
    value = _OPEN_TAG.sub("\u2039", value)  # a leftover "<tag" can no longer open markup
    value = _SPACE.sub(" ", value)
    value = "\n".join(line.strip() for line in value.split("\n"))
    value = _BLANKS.sub("\n\n", value).strip()
    return value[:limit]


def public_http_url(value: str) -> str:
    """An http(s) url on a public host. Local, private and numeric-trick hosts are refused.

    Nobody may fetch a stored url outside M5: this only keeps obviously internal targets out.
    """
    value = value.strip()
    parts = urlsplit(value)
    if len(value) > MAX_URL or parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an http(s) url")
    if parts.username or parts.password:
        raise ValueError("url must not carry credentials")
    host = parts.hostname.lower().rstrip(".")
    if (
        host == "localhost"
        or host.endswith(_LOCAL_SUFFIXES)
        or ("." not in host and ":" not in host)
    ):
        raise ValueError("url host is local")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if _NUMERIC_HOST.fullmatch(host):
            raise ValueError("url host is a numeric trick") from None
        return value
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        raise ValueError("url host is not a public address")
    return value


ALLOWED_METADATA = frozenset(
    {
        "hn_id", "kind", "comments", "discussion_url", "stars", "forks", "topics", "language",
        "pushed_at", "archived", "search_topic", "tags", "site", "answers", "views", "answered",
        "licence", "query", "subreddit", "training_allowed", "retrieval_only", "ph_id",
    }
)  # fmt: skip
MAX_META_STR = 64
MAX_META_LIST = 30


def _scalar(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text(value, MAX_META_STR, strip_tags=True) or MISSING
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    return MISSING


def clean_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known keys; strings capped at 64 chars; lists of scalars; depth at most 2."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in ALLOWED_METADATA:
            continue
        if key == "discussion_url":
            with contextlib.suppress(ValueError):
                out[key] = public_http_url(str(value))
        elif isinstance(value, list):
            items = [_scalar(v) for v in value[:MAX_META_LIST]]
            out[key] = [v for v in items if v is not MISSING]
        else:
            scalar = _scalar(value)
            if scalar is not MISSING:
                out[key] = scalar
    return out


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
        return public_http_url(value)

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return clean_metadata(value)

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
