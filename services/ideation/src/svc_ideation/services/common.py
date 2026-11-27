"""Helpers shared by the agents: evidence gathering, id filtering, envelopes, idea views."""

from collections.abc import Iterable, Sequence
from typing import Any

from common.schemas import EvidenceItem

from ..clients.kb import KnowledgeBaseClient
from ..models.ideas import Idea

Query = tuple[str, list[str] | None]  # (query text, kinds filter)


async def gather_evidence(
    kb: KnowledgeBaseClient, run_id: str, queries: Sequence[Query], *, k: int, cap: int
) -> list[EvidenceItem]:
    """Run each M6 search, merge results without duplicates, keep at most ``cap`` items."""
    merged: dict[str, EvidenceItem] = {}
    for text, kinds in queries:
        for item in await kb.search(text, k=k, run_id=run_id, kinds=kinds):
            merged.setdefault(item.evidence_id, item)
    return list(merged.values())[:cap]


def keep_ids(ids: Iterable[str], allowed: set[str]) -> list[str]:
    """``ids`` restricted to ``allowed``, order kept, duplicates removed."""
    return [i for i in dict.fromkeys(ids) if i in allowed]


def envelope_list(key: str) -> Any:
    """Parser for ``{key: [...]}`` replies: the list, or None so the runner retries."""

    def parse(data: dict[str, Any]) -> list[Any] | None:
        value = data.get(key)
        return value if isinstance(value, list) else None

    return parse


def idea_view(idea: Idea) -> dict[str, Any]:
    """What a model may see about an idea: no earlier findings, no scores."""
    return {
        "idea_ref": idea.idea_ref,
        "title": idea.title,
        "summary": idea.summary,
        "target_user": idea.target_user,
        "core_features": idea.core_features,
        "est_scope": idea.est_scope,
    }


def normalise(text: str) -> str:
    return " ".join(text.lower().split())
