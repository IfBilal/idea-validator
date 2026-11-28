"""Tests for make_labelled_set.py and for the AI-draft file (not the human labelled set)."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import make_labelled_set as m  # noqa: E402

ROOT = HERE.parents[1]
CANDS = [json.loads(x) for x in (HERE / "candidates.jsonl").read_text().splitlines() if x.strip()]
DRAFT = [
    json.loads(x) for x in (HERE / "labelled_set.draft.jsonl").read_text().splitlines() if x.strip()
]
SNAPSHOT = ROOT / "data" / "snapshots" / "cards-20261127.jsonl.gz"
HEAD = (
    "| id | idea | profile | bilal | taimoor | resolved | resolved_by | category | notes "
    "| ai_draft | ai_confidence | ai_rationale | ai_cards |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)
CYCLE = list(m.VERDICTS)


def sheet(labels: list[tuple[str, str]], draft: str = "GO", extra: dict | None = None) -> str:
    rows = []
    for i, (b, t) in enumerate(labels):
        res = (extra or {}).get(i, ("", ""))
        rows.append(
            f"| L{i + 1:03d} | idea | prof | {b} | {t} | {res[0]} | {res[1]} |  |  "
            f"| {draft} | low | r | c |"
        )
    return HEAD + "\n".join(rows) + "\n"


def fake_candidates(n: int) -> dict[str, dict]:
    return {
        f"L{i + 1:03d}": {"id": f"L{i + 1:03d}", "idea_text": "t", "profile": {}} for i in range(n)
    }


def full_labels(n: int = 40) -> list[tuple[str, str]]:
    return [(CYCLE[i % 4], CYCLE[i % 4]) for i in range(n)]


def run(text: str, n: int = 40) -> tuple[list[dict], list[str]]:
    warn: list[str] = []
    return m.build(m.parse_worksheet(text), fake_candidates(n), warn), warn


def test_happy_path_agreement_and_resolution() -> None:
    labels = full_labels()
    labels[0] = ("GO", "PIVOT")
    out, _ = run(sheet(labels, extra={0: ("GO", "discussion")}))
    assert len(out) == 40
    assert out[0]["human_verdict"] == "GO"
    assert out[0]["resolved_by"] == "discussion"
    assert out[0]["labels"] == {"bilal": "GO", "taimoor": "PIVOT"}
    assert out[1]["resolved_by"] == "agreement"
    assert {o["category"] for o in out} == set(m.CATEGORIES)


@pytest.mark.parametrize("bad", ["", "go", "MAYBE"])
def test_refuses_empty_or_invalid_label(bad: str) -> None:
    labels = full_labels()
    labels[3] = (bad, "GO")
    with pytest.raises(m.WorksheetError, match="L004"):
        run(sheet(labels))


def test_refuses_unresolved_difference() -> None:
    labels = full_labels()
    labels[2] = ("GO", "PIVOT")
    with pytest.raises(m.WorksheetError, match="resolved"):
        run(sheet(labels))


def test_refuses_when_a_category_has_fewer_than_ten() -> None:
    with pytest.raises(m.WorksheetError, match="fewer than 10"):
        run(sheet([("GO", "GO")] * 40))


def test_warns_when_a_human_column_matches_the_ai_draft_everywhere() -> None:
    labels = [(CYCLE[i % 4], CYCLE[i % 4]) for i in range(40)]
    rows = m.parse_worksheet(sheet(labels))
    for r, (b, _) in zip(rows, labels, strict=True):
        r["ai_draft"] = b
    warn: list[str] = []
    m.build(rows, fake_candidates(40), warn)
    assert len(warn) == 2  # both humans match the draft on every row


def test_main_writes_file_and_refuses_bad_sheet(tmp_path: Path) -> None:
    ws = tmp_path / "w.md"
    cands = tmp_path / "c.jsonl"
    cands.write_text("".join(json.dumps(c) + "\n" for c in fake_candidates(40).values()))
    out = tmp_path / "o.jsonl"
    args = ["--worksheet", str(ws), "--candidates", str(cands), "--out", str(out)]
    ws.write_text(sheet(full_labels(), draft="PIVOT"))
    assert m.main(args) == 0
    assert len(out.read_text().splitlines()) == 40
    out.unlink()
    ws.write_text(sheet([("", "")] * 40))
    assert m.main(args) == 1
    assert not out.exists()


def test_committed_worksheet_parses_and_lists_every_candidate() -> None:
    rows = m.parse_worksheet((HERE / "REVIEW_WORKSHEET.md").read_text())
    assert [r["id"] for r in rows] == [c["id"] for c in CANDS]


# ---- AI draft file checks -------------------------------------------------


def test_draft_ids_and_text_match_candidates() -> None:
    assert [d["id"] for d in DRAFT] == [c["id"] for c in CANDS]
    for d, c in zip(DRAFT, CANDS, strict=True):
        assert d["idea_text"] == c["idea_text"]
        assert d["profile"] == c["profile"]


def test_draft_enums_and_shape() -> None:
    for d in DRAFT:
        assert d["draft_verdict"] in m.VERDICTS
        assert d["category"] in m.CATEGORIES
        assert d["confidence"] in {"low", "medium", "high"}
        assert d["labelled_by"] == "ai-draft"
        assert d["rationale"].count(". ") <= 3 and d["rationale"]
        assert len(d["evidence_card_ids"]) <= 5
        assert "labels" not in d and "human_verdict" not in d


def test_draft_has_no_human_names() -> None:
    blob = (HERE / "labelled_set.draft.jsonl").read_text().lower()
    assert "bilal" not in blob
    assert "taimoor" not in blob


def test_draft_card_ids_exist_in_snapshot() -> None:
    if not SNAPSHOT.exists():
        pytest.skip("snapshot missing")
    with gzip.open(SNAPSHOT, "rt") as f:
        ids = {json.loads(line)["id"] for line in f}
    for d in DRAFT:
        assert set(d["evidence_card_ids"]) <= ids, d["id"]
