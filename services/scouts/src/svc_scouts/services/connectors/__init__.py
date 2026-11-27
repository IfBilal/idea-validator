"""One connector per source, keyed by the source name used in ``config/sources.yaml``."""

from .base import Connector
from .github import GitHub
from .hackernews import HackerNews
from .lobsters import Lobsters
from .producthunt import ProductHunt
from .reddit import Reddit
from .stackexchange import StackExchange

CONNECTORS: dict[str, Connector] = {
    c.name: c
    for c in (HackerNews(), GitHub(), Lobsters(), StackExchange(), Reddit(), ProductHunt())
}
ALIASES = {"hn": "hackernews", "ph": "producthunt", "se": "stackexchange"}

__all__ = ["ALIASES", "CONNECTORS", "Connector"]
