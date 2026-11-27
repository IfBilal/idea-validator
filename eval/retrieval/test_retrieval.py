"""Retrieval quality gate (M6-06): the labelled queries against the committed snapshot.

Fails if hybrid+rerank Recall@5 or MRR@10 falls more than 0.02 below ``baseline.json``. The baseline
changes only in a reviewed PR with a reason. The two quality tests use the real FastEmbed models
(downloaded once; minutes on a busy CPU), so they carry the ``retrieval`` marker: make test skips
them; ``make test-retrieval`` and the retrieval-gate workflow run them.
"""

import asyncio
import json

import pytest
from svc_kb.services.models import FastEmbedModels

from eval.retrieval.runner import (
    BASELINE,
    MODES,
    build_index,
    evaluate,
    load_cards,
    load_queries,
    newest_snapshot,
)

TOLERANCE = 0.02


def test_every_query_is_labelled_against_cards_that_exist() -> None:
    data = load_queries()
    queries = data["queries"]
    assert len(queries) >= 20
    assert data["labelled_by"], "say who labelled the relevance judgements"
    ids = {c["id"] for c in load_cards(newest_snapshot())}
    for q in queries:
        assert q["relevant"], f"{q['id']} has no relevant cards: unlabelled queries fail on purpose"
        assert set(q["relevant"].values()) <= {1, 2}, q["id"]
        missing = set(q["relevant"]) - ids
        assert not missing, (
            f"{q['id']} labels cards that are not in the snapshot: {sorted(missing)}"
        )


async def _run() -> dict[str, object]:
    index = await build_index(load_cards(newest_snapshot()), FastEmbedModels())
    return await evaluate(index, load_queries()["queries"])


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    return asyncio.run(_run())  # indexed once for both tests


@pytest.mark.retrieval
def test_hybrid_rerank_does_not_regress_against_the_baseline(result: dict[str, object]) -> None:
    baseline = json.loads(BASELINE.read_text())["hybrid_rerank"]
    got = result["modes"]["hybrid+rerank"]  # type: ignore[index]
    assert got["recall@5"] >= baseline["recall@5"] - TOLERANCE, (got, baseline)
    assert got["mrr@10"] >= baseline["mrr@10"] - TOLERANCE, (got, baseline)


@pytest.mark.retrieval
def test_fusion_and_rerank_are_worth_their_cost(result: dict[str, object]) -> None:
    """ADR-002: hybrid beats both single legs on recall@10; rerank does not lose to hybrid."""
    modes = result["modes"]
    assert set(modes) == set(MODES)  # type: ignore[arg-type]
    hybrid = modes["hybrid"]  # type: ignore[index]
    assert hybrid["recall@10"] > modes["vector"]["recall@10"]  # type: ignore[index]
    assert hybrid["recall@10"] > modes["keyword"]["recall@10"]  # type: ignore[index]
    assert modes["hybrid+rerank"]["mrr@10"] >= hybrid["mrr@10"] - TOLERANCE  # type: ignore[index]
