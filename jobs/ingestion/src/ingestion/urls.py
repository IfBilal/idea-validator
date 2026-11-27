"""Canonical page URLs: the identity of a card.

Builds on ``common.ids.canonical_url`` (which M6 applies again, so the id the job predicts is the id
M6 mints): lower-case host, tracking parameters, fragment, default port and trailing slash are
already handled there. This adds the known mirror hosts that name the same page, so the same
Reddit thread reached through ``old.reddit.com`` and ``www.reddit.com`` is one card.
"""

from urllib.parse import urlsplit, urlunsplit

from common.ids import canonical_url

# mirror host -> the one host we keep. Exact matches only: never a suffix or a substring.
_MIRRORS = {
    "www.github.com": "github.com",
    "old.reddit.com": "reddit.com",
    "np.reddit.com": "reddit.com",
    "new.reddit.com": "reddit.com",
    "m.reddit.com": "reddit.com",
    "i.reddit.com": "reddit.com",
    "www.reddit.com": "reddit.com",
    "www.producthunt.com": "producthunt.com",
    "www.lobste.rs": "lobste.rs",
}


def normalise_url(raw: str) -> str:
    """The one spelling of a page's URL; ``ValueError`` if it is not an http(s) page."""
    first = canonical_url(raw)
    parts = urlsplit(first)
    host = parts.hostname or ""
    mapped = _MIRRORS.get(host)
    if mapped is None:
        return first
    netloc = mapped if parts.port is None else f"{mapped}:{parts.port}"
    return canonical_url(urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")))
