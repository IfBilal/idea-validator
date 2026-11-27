"""Raw scout item -> ``CardDraft``: normalise, scrub, classify. Pure and deterministic.

Rules, not a model: the same item always gives the same card (CLAUDE.md rule 1 spirit, and the
precondition for "re-running ingestion adds 0 duplicates"). Private data never reaches a card:
author handles and e-mail addresses are dropped or masked (docs/harness/10 section 1), and only a
whitelist of metadata keys (topics, language, ...) is read.
"""

import hashlib
import re
from datetime import UTC, date, datetime
from typing import Any, get_args

from common.schemas.types import Kind
from ingestion.models import MAX_TEXT_CHARS, CardDraft, RawItem, Stage
from ingestion.urls import normalise_url

MAX_NAME = 300
MAX_SUMMARY = 300
MAX_PROBLEM = 600
MAX_TAGS = 20
MAX_TAG_LEN = 60
_MAX_INPUT = 400_000  # cut before any regex runs: a hostile body costs almost nothing

_HIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁠-⁤﻿]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLE = re.compile(r"(?<![\w])/?(?:u|user)/[A-Za-z0-9_-]{2,40}")
_SPACES = re.compile(r"[^\S\n]+")
_BLANKS = re.compile(r"\n{3,}")
_HN_PREFIX = re.compile(r"^\s*(?:show|ask|launch|tell)\s+hn\s*[:\-\u2013\u2014]\s*", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_SLUG_JUNK = re.compile(r"[^\w+#.\-]+")

_PRODUCT_TITLE = re.compile(
    r"^\s*(?:show|launch)\s+hn\b|^\s*(?:i|we)\s+(?:built|made|launched|created|shipped)\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\bis there (?:a|an|any)\b|\blooking for\b|\bi wish\b|\bwish (?:there|i)\b"
    r"|\balternatives? to\b|\banyone know\b|\bany (?:tool|app|service)\b|\brecommend(?:ation)?s?\b"
    r"|\bhow (?:do|can|to) i\b|\bneed an? \b",
    re.IGNORECASE,
)
_COMPLAINT = re.compile(
    r"\bhate\b|\bfrustrat|\bannoying\b|\bsucks?\b|\bbroken\b|\bterrible\b|\bpainful\b"
    r"|\bwhy is\b|\bfed up\b|\bnightmare\b",
    re.IGNORECASE,
)
_PRODUCT_FLAIRS = frozenset({"show", "show off", "launch", "i made this", "product", "showcase"})
_STAGES: frozenset[str] = frozenset(get_args(Stage))
_TAG_KEYS = ("topics", "tags", "labels", "categories", "language", "subreddit", "category")


class Rejected(Exception):
    """The item cannot become a card; ``reason`` is recorded in the run, never raised further."""

    def __init__(self, reason: str, url: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.url = url


def _clean(text: str) -> str:
    text = _HIDDEN.sub("", text[:_MAX_INPUT])
    text = _EMAIL.sub("[email removed]", text)
    text = _HANDLE.sub("[user removed]", text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


def _cut(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a word boundary with an ellipsis."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    if " " in head[limit // 2 :]:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;:-") + "…"


def classify_kind(item: RawItem) -> Kind:
    """Deterministic kind from the source and the title (docs/harness/10 section 5)."""
    if item.source == "github":
        return "repo"
    if item.source in ("producthunt", "yc"):
        return "product"
    title = _clean(item.title)
    flair = str(item.metadata.get("flair", "")).strip().lower()
    if _PRODUCT_TITLE.search(title) or flair in _PRODUCT_FLAIRS:
        return "product"
    if _REQUEST.search(title):
        return "request"
    if _COMPLAINT.search(title):
        return "complaint"
    if item.source == "stackexchange" and title:
        return "request"  # a question is a need someone could not meet
    return "discussion"


def _day(value: datetime | date | None) -> date | None:
    if isinstance(value, datetime):
        return (value.astimezone(UTC) if value.tzinfo else value).date()
    return value


def _tags(metadata: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    for key in _TAG_KEYS:
        value = metadata.get(key)
        if isinstance(value, str):
            raw.append(value)
        elif isinstance(value, list | tuple):
            raw.extend(v for v in value if isinstance(v, str))
    seen: dict[str, None] = {}
    for tag in raw:
        slug = _SLUG_JUNK.sub("-", _HIDDEN.sub("", tag).strip().lower()).strip("-")[:MAX_TAG_LEN]
        slug = slug.strip("-")
        if slug:
            seen.setdefault(slug)
    return list(seen)[:MAX_TAGS]


def content_hash_of(text: str) -> str:
    # same normalisation as M6 (svc_kb.models.cards.content_hash_of): a contract test pins it
    return hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()


def map_item(item: RawItem, *, fetched_on: date) -> CardDraft:
    """Build the card for one raw item, or raise ``Rejected`` with the reason."""
    try:
        url = normalise_url(item.url)
    except ValueError as exc:
        raise Rejected(f"bad url: {exc}", item.url[:200]) from None
    title = _HN_PREFIX.sub("", _clean(item.title)).strip()
    body = _clean(item.text)
    if not title and not body:
        raise Rejected("no title and no text", url)
    kind = classify_kind(item)
    if not title:
        title = _SENTENCE_END.split(body, maxsplit=1)[0]
    name = _cut(" ".join(title.split()), MAX_NAME)
    first = _SENTENCE_END.split(body, maxsplit=1)[0] if body else name
    summary = _cut(" ".join(first.split()), MAX_SUMMARY)
    text = f"{name}\n\n{body}" if body else name
    text = text[:MAX_TEXT_CHARS]
    fetched = _day(item.fetched_at) or fetched_on
    published = _day(item.created_at)
    if published is not None and published > fetched:
        published = None  # a date from the future is a scraping artefact, not a fact
    stage: str = "unknown"
    if item.source == "producthunt":
        stage = "launched"
    elif item.source == "yc":
        declared = str(item.metadata.get("stage", "")).strip().lower()
        stage = declared if declared in _STAGES else "unknown"
    return CardDraft(
        name=name,
        summary=summary,
        problem=_cut(body, MAX_PROBLEM) if kind in ("complaint", "request") and body else None,
        niche_tags=_tags(item.metadata),
        stage=stage,
        source=item.seen_on,
        kind=kind,
        url=url,
        published_at=published,
        fetched_at=fetched,
        text=text,
        content_hash=content_hash_of(text),
    )
