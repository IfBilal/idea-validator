"""Card and evidence ids, shared by ingestion (which writes them) and M6 (which serves them).

``card_id = "card_" + sha1(canonical_url)[:8]`` (docs/harness/04 section 2). The same page reached
through different URLs (tracking parameters, a fragment, a trailing slash, a default port, host
case) must give the same id, otherwise ingestion stores duplicates that the exact-hash dedupe
cannot see. Changing this scheme orphans every stored card and Qdrant point, so a test pins a
known value.

An evidence id names one chunk of a card: ``card_<8 hex>#c<n>``.
"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def card_id_for(url: str) -> str:
    digest = hashlib.sha1(canonical_url(url).encode(), usedforsecurity=False).hexdigest()
    return f"card_{digest[:8]}"


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
