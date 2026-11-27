import gzip
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from ingest_fakes import FakeKb, read_gz_text
from synthetic import synthetic_items, write_jsonl

from ingestion.dedupe import DedupeConfig
from ingestion.mappers import content_hash_of
from ingestion.pipeline import run_ingestion
from ingestion.snapshot import SnapshotError, latest_snapshot, restore_snapshot, write_snapshot
from ingestion.sources import JsonlSource
from ingestion.state import StateStore

DAY = date(2026, 3, 1)


async def filled_kb(tmp_path: Path, n: int = 60) -> FakeKb:
    kb = FakeKb()
    items = list(synthetic_items(n, seed=4))
    # one page cross-posted on a second platform
    items.append(dict(items[1], source="reddit", title="same page, other platform"))
    await run_ingestion(
        JsonlSource(write_jsonl(tmp_path / "raw.jsonl", items)),
        kb,
        state=StateStore(tmp_path / "state"),
        dedupe_config=DedupeConfig(),
        today=DAY,
    )
    return kb


async def test_snapshot_writes_a_sorted_jsonl_gz_and_a_manifest(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path)
    out = tmp_path / "snaps"
    manifest = await write_snapshot(kb, out, day=DAY, page_size=7)
    path = out / "cards-20260301.jsonl.gz"
    assert path.exists() and (out / "cards-20260301.manifest.json").exists()
    rows = [json.loads(line) for line in read_gz_text(path).splitlines()]
    assert len(rows) == manifest.total == 60
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert manifest.by_source == {"github": 15, "hackernews": 15, "producthunt": 15, "reddit": 15}
    assert manifest.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert sum(manifest.by_platform.values()) == 61  # the cross-posted page counts twice
    assert "first_seen_at" not in rows[0]  # volatile fields are left out: snapshots are stable


async def test_two_snapshots_of_the_same_kb_are_byte_identical(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path)
    a = await write_snapshot(kb, tmp_path / "a", day=DAY)
    b = await write_snapshot(kb, tmp_path / "b", day=DAY)
    assert a.sha256 == b.sha256


async def test_snapshot_holds_no_author_or_email_data(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path)
    await write_snapshot(kb, tmp_path / "s", day=DAY)
    text = read_gz_text(tmp_path / "s" / "cards-20260301.jsonl.gz")
    assert "author" not in text and "@" not in text


async def test_an_empty_kb_is_refused_so_it_cannot_overwrite_a_good_snapshot(
    tmp_path: Path,
) -> None:
    with pytest.raises(SnapshotError, match="empty"):
        await write_snapshot(FakeKb(), tmp_path / "s", day=DAY)
    assert not (tmp_path / "s" / "cards-20260301.jsonl.gz").exists()


async def test_restore_into_a_fresh_kb_recreates_every_card_and_platform(tmp_path: Path) -> None:
    source_kb = await filled_kb(tmp_path)
    await write_snapshot(source_kb, tmp_path / "s", day=DAY)
    fresh = FakeKb()
    report = await restore_snapshot(
        fresh, tmp_path / "s" / "cards-20260301.jsonl.gz", batch_size=20
    )
    assert report.status == "ok"
    assert (report.added, report.dropped, report.id_mismatches) == (60, 0, 0)
    assert fresh.cards.keys() == source_kb.cards.keys()
    assert fresh.platforms == source_kb.platforms
    assert all(len(b) <= 20 for b in fresh.calls)


async def test_restore_twice_changes_nothing(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path)
    await write_snapshot(kb, tmp_path / "s", day=DAY)
    fresh = FakeKb()
    path = tmp_path / "s" / "cards-20260301.jsonl.gz"
    await restore_snapshot(fresh, path)
    before = {u: dict(c) for u, c in fresh.cards.items()}
    again = await restore_snapshot(fresh, path)
    assert (again.added, again.updated) == (0, 0)
    assert again.unchanged == 61
    assert fresh.cards == before


async def test_snapshot_then_restore_then_snapshot_is_identical(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path)
    first = await write_snapshot(kb, tmp_path / "one", day=DAY)
    fresh = FakeKb()
    await restore_snapshot(fresh, tmp_path / "one" / "cards-20260301.jsonl.gz")
    second = await write_snapshot(fresh, tmp_path / "two", day=DAY)
    assert second.sha256 == first.sha256


def rewrite(path: Path, lines: list[str], *, manifest: bool = False) -> Path:
    target = path.parent / "edited.jsonl.gz"
    target.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    return target


async def test_restore_skips_malformed_and_tampered_rows_and_reports_them(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path, n=8)
    await write_snapshot(kb, tmp_path / "s", day=DAY)
    path = tmp_path / "s" / "cards-20260301.jsonl.gz"
    lines = read_gz_text(path).splitlines()
    tampered = json.loads(lines[0])
    tampered["text"] = "changed after the hash was taken"
    bad = [json.dumps(tampered), "{broken", "[]", json.dumps({"name": "x"}), "", *lines[1:]]
    fresh = FakeKb()
    report = await restore_snapshot(fresh, rewrite(path, bad), allow_unverified=True)
    assert report.added == 7 and report.dropped == 4
    assert len(fresh.cards) == 7


async def test_restore_detects_a_corrupt_archive_and_a_wrong_checksum(tmp_path: Path) -> None:
    kb = await filled_kb(tmp_path, n=8)
    await write_snapshot(kb, tmp_path / "s", day=DAY)
    path = tmp_path / "s" / "cards-20260301.jsonl.gz"
    data = path.read_bytes()
    broken = tmp_path / "s" / "cards-20260302.jsonl.gz"
    broken.write_bytes(data[: len(data) // 2])
    with pytest.raises(SnapshotError):
        await restore_snapshot(FakeKb(), broken)
    (tmp_path / "s" / "cards-20260302.manifest.json").write_text(
        json.dumps({"sha256": "0" * 64, "total": 8})
    )
    other = tmp_path / "s" / "cards-20260302.jsonl.gz"
    other.write_bytes(data)
    with pytest.raises(SnapshotError, match="checksum"):
        await restore_snapshot(FakeKb(), other)


async def test_restore_of_a_missing_or_empty_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        await restore_snapshot(FakeKb(), tmp_path / "nope.jsonl.gz")
    empty = tmp_path / "e.jsonl.gz"
    empty.write_bytes(gzip.compress(b""))
    with pytest.raises(SnapshotError, match="no cards"):
        await restore_snapshot(FakeKb(), empty, allow_unverified=True)


def test_latest_snapshot_picks_the_newest_dated_file(tmp_path: Path) -> None:
    for name in ("cards-20260101.jsonl.gz", "cards-20260301.jsonl.gz", "cards-20260215.jsonl.gz"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "README.md").write_text("x")
    assert latest_snapshot(tmp_path).name == "cards-20260301.jsonl.gz"
    assert latest_snapshot(tmp_path / "cards-20260101.jsonl.gz").name == "cards-20260101.jsonl.gz"
    with pytest.raises(SnapshotError, match="no snapshot"):
        latest_snapshot(tmp_path / "missing")
    (tmp_path / "x").mkdir()
    with pytest.raises(SnapshotError, match="no snapshot"):
        latest_snapshot(tmp_path / "x")


def test_the_hash_the_job_uses_matches_whitespace_and_case_insensitively() -> None:
    assert content_hash_of("A  b\n") == content_hash_of("a B")
