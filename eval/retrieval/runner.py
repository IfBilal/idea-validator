"""Run the labelled queries against the committed snapshot in all four retrieval modes.

The snapshot is loaded into an in-memory Qdrant with the real FastEmbed models, so the numbers are
what M6 serves, without Postgres or a cluster. ``python -m eval.retrieval.runner`` writes
``eval/results/retrieval-<date>.json``; ``--pool`` prints candidates for labelling instead.
"""

import asyncio
import gzip
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from qdrant_client import AsyncQdrantClient
from svc_kb.services.chunking import chunk_text
from svc_kb.services.models import DENSE_DIM, FastEmbedModels
from svc_kb.services.search import ChunkIndex, IndexedCard, SearchFilters

from eval.retrieval.metrics import dedupe, ndcg_at, recall_at, reciprocal_rank_at

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = ROOT / "data" / "snapshots"
QUERIES = Path(__file__).with_name("queries.yaml")
BASELINE = Path(__file__).with_name("baseline.json")
MODES = ("vector", "keyword", "hybrid", "hybrid+rerank")
POOL_CHUNKS = 40  # chunks fetched per query before they are folded into cards


def newest_snapshot(directory: Path = SNAPSHOTS) -> Path:
    files = sorted(directory.glob("cards-*.jsonl.gz"))
    if not files:
        raise FileNotFoundError(f"no snapshot in {directory}")
    return files[-1]


def load_cards(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def build_index(cards: Sequence[Mapping[str, Any]], models: Any) -> ChunkIndex:
    index = ChunkIndex(AsyncQdrantClient(":memory:"), models, collection="eval", dim=DENSE_DIM)
    await index.ensure_collection()
    for card in cards:
        await index.upsert(
            IndexedCard(
                card_id=card["id"],
                url=card["url"],
                source=card["source"],
                kind=card["kind"],
                niche_tags=list(card["niche_tags"]),
                published_at=date.fromisoformat(card["published_at"])
                if card["published_at"]
                else None,
                chunks=chunk_text(card["id"], card["text"]),
                card_name=card["name"],
            )
        )
    return index


def to_filters(raw: Mapping[str, Any]) -> SearchFilters:
    def day(key: str) -> date | None:
        value = raw.get(key)
        return date.fromisoformat(str(value)) if value else None

    niches = raw.get("niches") or ([raw["niche"]] if raw.get("niche") else None)
    return SearchFilters(
        sources=raw.get("sources"),
        kinds=raw.get("kinds"),
        niches=niches,
        published_after=day("date_from"),
        published_before=day("date_to"),
    )


async def ranked_cards(index: ChunkIndex, query: Mapping[str, Any], mode: str, k: int) -> list[str]:
    hits = await index.search(
        query["query"],
        mode=mode,
        k=POOL_CHUNKS,
        filters=to_filters(query.get("filters") or {}),  # type: ignore[arg-type]
    )
    return dedupe([h.card_id for h in hits])[:k]


def load_queries(path: Path = QUERIES) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


async def evaluate(index: ChunkIndex, queries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    labelled = [q for q in queries if q.get("relevant")]
    modes: dict[str, dict[str, float]] = {}
    for mode in MODES:
        scores: dict[str, list[float]] = {
            "recall@5": [],
            "recall@10": [],
            "mrr@10": [],
            "ndcg@10": [],
        }
        for q in labelled:
            ranked = await ranked_cards(index, q, mode, 10)
            rel = {str(cid): int(g) for cid, g in q["relevant"].items()}
            r5, r10 = recall_at(ranked, rel, 5), recall_at(ranked, rel, 10)
            assert r5 is not None and r10 is not None
            scores["recall@5"].append(r5)
            scores["recall@10"].append(r10)
            scores["mrr@10"].append(reciprocal_rank_at(ranked, rel, 10))
            scores["ndcg@10"].append(ndcg_at(ranked, rel, 10))
        modes[mode] = {m: round(sum(v) / len(v), 4) for m, v in scores.items()}
    return {"queries": len(labelled), "modes": modes}


async def _pool(
    index: ChunkIndex, cards: Mapping[str, Mapping[str, Any]], queries: Sequence[Any]
) -> None:
    """Print the union of every mode's top results per query, for a human to grade."""
    for q in queries:
        seen: dict[str, None] = {}
        for mode in MODES:
            for cid in await ranked_cards(index, q, mode, 10):
                seen.setdefault(cid)
        print(f"\n### {q['id']}: {q['query']}  filters={q.get('filters') or {}}")
        for cid in seen:
            c = cards[cid]
            body = " ".join(c["text"].split())[:150]
            print(f"{cid} [{c['source']}/{c['kind']}] {c['name'][:70]} :: {body}")


async def main(argv: Sequence[str]) -> None:
    cards = load_cards(newest_snapshot())
    index = await build_index(cards, FastEmbedModels())
    data = load_queries()
    if "--pool" in argv:
        todo = [q for q in data["queries"] if not q.get("relevant")]
        await _pool(index, {c["id"]: c for c in cards}, todo)
        return
    result = await evaluate(index, data["queries"])
    result["snapshot"] = newest_snapshot().name
    out = ROOT / "eval" / "results" / f"retrieval-{date.today().isoformat()}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
