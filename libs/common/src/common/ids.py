"""Card and evidence ids, shared by ingestion (which writes them) and M6 (which serves them).

``card_id = "card_" + sha1(canonical_url)[:8]`` (docs/harness/04 section 2). The same page reached
through different URLs (tracking parameters, a fragment, a trailing slash, a default port, host
case) must give the same id, otherwise ingestion stores duplicates that the exact-hash dedupe
cannot see. Changing this scheme orphans every stored card and Qdrant point, so a test pins a
known value.

Eight hex characters are only 32 bits: harmless by chance at a few thousand cards, but a crafted URL
could be made to collide and overwrite a legitimate card on upsert. The id is therefore minted with
``assign_card_id``, which looks the id up first and, if a DIFFERENT page already owns it, extends
the id to 10 and then 12 hex characters (the schema allows 8 to 12). Existing 8-character ids stay
valid because every longer id starts with the shorter one.

An evidence id names one chunk of a card: ``card_<8 hex>#c<n>``.
"""

import hashlib
import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ID_LENGTHS = (8, 10, 12)  # hex characters; the schemas accept 8 to 12

_DEFAULT_PORTS = {"http": 80, "https": 443}
_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid", "igshid", "yclid"}
)
_CARD_ID = re.compile(r"^card_[0-9a-f]{8,12}$")
_EVIDENCE_ID = re.compile(r"^(card_[0-9a-f]{8,12})#c(\d+)$")


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_KEYS


def canonical_url(raw: str) -> str:
    """The one spelling of a page's URL: what identifies the page and nothing else."""
    text = raw.strip()
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError(f"not an http(s) page url: {raw[:80]!r}")
    host = parts.hostname.lower()
    if ":" in host:  # an IPv6 literal keeps its brackets
        host = f"[{host}]"
    port = parts.port  # raises ValueError for a malformed port
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    kept = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    )
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def _sha1_hex(canonical: str) -> str:
    return hashlib.sha1(canonical.encode(), usedforsecurity=False).hexdigest()


def card_id_for(url: str, *, length: int = 8) -> str:
    """``card_`` + the first ``length`` (8, 10 or 12) hex characters of sha1(canonical url)."""
    if length not in ID_LENGTHS:
        raise ValueError(f"card id length must be one of {ID_LENGTHS}, got {length}")
    return f"card_{_sha1_hex(canonical_url(url))[:length]}"


def assign_card_id(
    url: str,
    owner_of: Callable[[str], str | None],
    *,
    digest: Callable[[str], str] = _sha1_hex,
) -> str:
    """The shortest id for ``url`` that no OTHER page owns.

    ``owner_of(card_id)`` returns the canonical URL stored under that id, or ``None`` if it is free.
    A free id, or one already owned by this very page (re-ingesting it), is used as is. If a
    different page owns it, the next longer id is tried, so a crafted URL cannot take over an
    existing card. ``digest`` is injectable so tests can force a collision.
    """
    canonical = canonical_url(url)
    full = digest(canonical)
    for length in ID_LENGTHS:
        candidate = f"card_{full[:length]}"
        owner = owner_of(candidate)
        if owner is None or owner == canonical:
            return candidate
    raise ValueError(f"card id collision at every length for {canonical[:80]!r}")


def evidence_id(card_id: str, chunk: int) -> str:
    if not _CARD_ID.match(card_id):
        raise ValueError(f"not a card id: {card_id!r}")
    if chunk < 0:
        raise ValueError(f"chunk number must be 0 or more, got {chunk}")
    return f"{card_id}#c{chunk}"


def parse_evidence_id(value: str) -> tuple[str, int]:
    match = _EVIDENCE_ID.fullmatch(value)
    if match is None:
        raise ValueError(f"not an evidence id: {value[:80]!r}")
    return match.group(1), int(match.group(2))
