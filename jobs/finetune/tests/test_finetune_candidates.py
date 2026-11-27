import json
from pathlib import Path
from typing import Any

import pytest

from finetune.candidates import (
    IdeaSeed,
    build_candidates,
    load_seeds,
    write_candidates,
)

DIMS = ("demand_pain", "competition", "differentiation", "feasibility", "risk", "learning")


class FakeRetriever:
    def __init__(self, hits: dict[str, list[tuple[str, str]]] | None = None) -> None:
        self.hits = hits or {}
        self.queries: list[tuple[str, str, int]] = []

    def search(self, query: str, *, dimension: str, k: int) -> list[tuple[str, str]]:
        self.queries.append((query, dimension, k))
        return self.hits.get(query, [("card_8f21aa01#c0", f"quote about {query}")])[:k]


def seeds(n: int = 3) -> list[IdeaSeed]:
    return [
        IdeaSeed(idea_id=f"i{i}", text=f"idea number {i}", source="product_card") for i in range(n)
    ]


def test_every_seed_gets_one_row_per_dimension() -> None:
    rows = build_candidates(seeds(3), FakeRetriever(), k=4)
    assert len(rows) == 18
    assert {r["dimension"] for r in rows} == set(DIMS)
    assert all(r["idea_id"] in {"i0", "i1", "i2"} for r in rows)


def test_rows_carry_evidence_features_and_source() -> None:
    rows = build_candidates(seeds(1), FakeRetriever(), k=4)
    row = rows[0]
    assert row["evidence"][0]["evidence_id"] == "card_8f21aa01#c0"
    assert row["features"]["n_sources"] == 1
    assert row["source"] == "product_card"
    assert row["idea_text"] == "idea number 0"


def test_retrieval_uses_production_k_and_the_dimension() -> None:
    r = FakeRetriever()
    build_candidates(seeds(1), r, k=5)
    assert all(k == 5 for _, _, k in r.queries)
    assert {d for _, d, _ in r.queries} == set(DIMS)


def test_idea_with_no_evidence_still_yields_an_abstain_candidate() -> None:
    r = FakeRetriever({"idea number 0": []})
    rows = build_candidates(seeds(1), r, k=4)
    assert all(row["evidence"] == [] for row in rows)


def test_duplicate_seed_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        build_candidates([*seeds(1), *seeds(1)], FakeRetriever(), k=4)


def test_evidence_ids_must_match_the_production_format() -> None:
    r = FakeRetriever({"idea number 0": [("not-an-id", "q")]})
    with pytest.raises(ValueError, match="evidence id"):
        build_candidates(seeds(1), r, k=4)


def test_labelled_ideas_are_excluded() -> None:
    three = [
        IdeaSeed("i0", "a recipe sharing app for families", "gap"),
        IdeaSeed("i1", "an invoice generator for freelancers", "gap"),
        IdeaSeed("i2", "a bus tracker for rural villages", "gap"),
    ]
    labelled = [{"id": "L1", "idea_text": "An invoice generator for freelancers!"}]
    rows = build_candidates(three, FakeRetriever(), k=4, labelled=labelled)
    assert "i1" not in {r["idea_id"] for r in rows}
    assert len(rows) == 12


def test_seed_loader_and_writer_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "seeds.jsonl"
    p.write_text(
        "\n".join(
            json.dumps({"idea_id": f"s{i}", "text": f"t{i}", "source": "paraphrase"})
            for i in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_seeds(p)
    assert [s.idea_id for s in loaded] == ["s0", "s1"]
    out = tmp_path / "c.jsonl"
    rows: list[dict[str, Any]] = build_candidates(loaded, FakeRetriever(), k=2)
    write_candidates(rows, out)
    assert len(out.read_text().splitlines()) == 12


def test_reddit_derived_seeds_are_refused() -> None:
    with pytest.raises(ValueError, match="reddit"):
        build_candidates([IdeaSeed(idea_id="r1", text="x", source="reddit")], FakeRetriever(), k=2)
