"""Candidate builder (P8-T01): ideas x dimensions with the evidence production would retrieve.

The retriever is a port: the production adapter lives with the KB (``services/kb``) and must be the
same code the live scorers use. Ideas come from product cards, gap-derived ideas and paraphrased
user-style ideas (docs/harness/11 §1). Reddit-derived text is refused outright: the platform's
terms forbid using it to train models (docs/data-sources.md).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from common.schemas import DIMENSIONS

from .schema import EVIDENCE_ID
from .split import SIMILARITY_LIMIT, similarity

PRODUCTION_K = 6
FORBIDDEN_SOURCES = frozenset({"reddit"})


@dataclass(frozen=True)
class IdeaSeed:
    idea_id: str
    text: str
    source: str  # product_card | gap | paraphrase
    profile: dict[str, Any] | None = None


class Retriever(Protocol):
    def search(self, query: str, *, dimension: str, k: int) -> list[tuple[str, str]]: ...


def load_seeds(path: Path) -> list[IdeaSeed]:
    seeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            seeds.append(IdeaSeed(raw["idea_id"], raw["text"], raw["source"], raw.get("profile")))
    return seeds


def build_candidates(
    seeds: Sequence[IdeaSeed],
    retriever: Retriever,
    *,
    k: int = PRODUCTION_K,
    labelled: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    ids = [s.idea_id for s in seeds]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate idea ids in the seeds")
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        if seed.source.lower() in FORBIDDEN_SOURCES:
            raise ValueError("reddit-derived seeds must never be used for training")
        if any(
            similarity(seed.text, str(item.get("idea_text", ""))) >= SIMILARITY_LIMIT
            for item in labelled
        ):
            continue  # an idea from the labelled verdict set is never a training idea
        for dimension in DIMENSIONS:
            hits = retriever.search(seed.text, dimension=dimension, k=k)
            for evidence_id, _ in hits:
                if not EVIDENCE_ID.match(evidence_id):
                    raise ValueError(
                        f"evidence id {evidence_id!r} is not in the card_<hex>#c<n> form"
                    )
            cards = {evidence_id.split("#", 1)[0] for evidence_id, _ in hits}
            rows.append(
                {
                    "idea_id": seed.idea_id,
                    "idea_text": seed.text,
                    "dimension": dimension,
                    "rubric_id": f"{dimension}@v1",
                    "evidence": [{"evidence_id": i, "quote": q} for i, q in hits],
                    "features": {"n_sources": len(cards)},
                    "profile": seed.profile if dimension in {"feasibility", "learning"} else None,
                    "source": seed.source,
                }
            )
    return rows


def write_candidates(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
