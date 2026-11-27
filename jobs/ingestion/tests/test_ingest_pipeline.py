import json
import random
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from ingest_fakes import FakeKb
from synthetic import synthetic_items, write_jsonl

from ingestion.dedupe import DedupeConfig
from ingestion.pipeline import RunReport, run_ingestion
from ingestion.sources import JsonlSource
from ingestion.state import StateStore
from ingestion.urls import normalise_url

TODAY = date(2026, 3, 1)


def now() -> datetime:
    return datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def run(
    path: Path, kb: FakeKb, state_dir: Path, *, batch_size: int = 50, resume: bool = True
) -> RunReport:
    return await run_ingestion(
        JsonlSource(path),
        kb,
        state=StateStore(state_dir),
        dedupe_config=DedupeConfig(),
        batch_size=batch_size,
        today=TODAY,
        now=now,
        resume=resume,
    )


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    return write_jsonl(tmp_path / "raw.jsonl", list(synthetic_items(120, seed=3)))


async def test_a_first_run_stores_every_card_in_capped_batches(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    report = await run(raw, kb, tmp_path / "state")
    assert report.status == "ok"
    assert (report.added, report.updated, report.unchanged, report.dropped) == (120, 0, 0, 0)
    assert len(kb.cards) == 120
    assert all(len(batch) <= 50 for batch in kb.calls)
    assert len(kb.calls) == 3  # 50 + 50 + 20
    assert report.added_by_source == {
        "github": 30,
        "hackernews": 30,
        "producthunt": 30,
        "reddit": 30,
    }


async def test_rerunning_on_the_same_input_adds_nothing(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    first = await run(raw, kb, tmp_path / "state")
    snapshot = {u: dict(c) for u, c in kb.cards.items()}
    second = await run(raw, kb, tmp_path / "state")
    assert first.added == 120
    assert (second.added, second.updated, second.dropped) == (0, 0, 0)
    assert second.unchanged == 120
    assert kb.cards == snapshot and len(kb.cards) == 120


async def test_rerunning_on_a_shuffled_input_adds_nothing(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    await run(raw, kb, tmp_path / "s1")
    lines = raw.read_text().splitlines()  # noqa: ASYNC240
    random.Random(9).shuffle(lines)  # noqa: S311
    shuffled = tmp_path / "shuffled.jsonl"
    shuffled.write_text("\n".join(lines) + "\n")
    again = await run(shuffled, kb, tmp_path / "s2")
    assert (again.added, again.updated) == (0, 0)
    assert len(kb.cards) == 120


async def test_input_with_duplicates_stores_each_card_once(tmp_path: Path) -> None:
    items = list(synthetic_items(20, seed=1))
    items += items[:5]  # exact repeats
    items += [dict(items[7], url=items[7]["url"] + "#frag")]  # same page, another spelling
    kb = FakeKb()
    report = await run(write_jsonl(tmp_path / "r.jsonl", items), kb, tmp_path / "st")
    assert len(kb.cards) == 20
    assert report.duplicates == 6
    again = await run(tmp_path / "r.jsonl", kb, tmp_path / "st")
    assert again.added == 0


async def test_a_page_seen_on_two_platforms_records_both(tmp_path: Path) -> None:
    gh = {"source": "github", "url": "https://github.com/o/r", "title": "o/r", "text": "A tool."}
    hn = {
        "source": "hackernews",
        "url": "https://www.github.com/o/r?utm_source=hn",
        "title": "Show HN: R",
        "text": "A tool.",
    }
    kb = FakeKb()
    report = await run(write_jsonl(tmp_path / "r.jsonl", [gh, hn]), kb, tmp_path / "st")
    assert len(kb.cards) == 1
    (platforms,) = kb.platforms.values()
    assert platforms == {"github", "hackernews"}
    assert report.added == 1
    again = await run(tmp_path / "r.jsonl", kb, tmp_path / "st")
    assert again.added == 0 and again.updated == 0


async def test_an_empty_input_is_a_clean_no_op(tmp_path: Path) -> None:
    kb = FakeKb()
    empty = tmp_path / "e.jsonl"
    empty.write_text("")
    report = await run(empty, kb, tmp_path / "st")
    assert report.status == "ok" and report.added == 0 and kb.calls == []


async def test_malformed_rows_are_counted_and_the_rest_is_ingested(tmp_path: Path) -> None:
    good = list(synthetic_items(5, seed=2))
    path = tmp_path / "m.jsonl"
    lines = [json.dumps(g) for g in good]
    lines[2:2] = [
        "{not json",
        "[1, 2]",
        '{"source": "myspace", "url": "https://a.com/x"}',
        '{"source": "github", "url": "nope", "title": "x"}',
        '{"source": "github", "url": "https://a.com/x", "title": "", "text": ""}',
        "",
    ]
    path.write_text("\n".join(lines) + "\n")
    kb = FakeKb()
    report = await run(path, kb, tmp_path / "st")
    assert report.added == 5
    assert report.rejected == 5
    assert len(report.rejections) == 5
    assert {r.reason.split(":")[0] for r in report.rejections} >= {"line is not valid JSON"}


async def test_unicode_and_huge_text_go_through(tmp_path: Path) -> None:
    items = [
        {
            "source": "reddit",
            "url": "https://a.com/日本",
            "title": "日本語のアプリ \U0001f680",
            "text": "word " * 300_000,
        }
    ]
    kb = FakeKb()
    report = await run(write_jsonl(tmp_path / "u.jsonl", items), kb, tmp_path / "st")
    assert report.added == 1
    (card,) = kb.cards.values()
    assert len(card["text"]) <= 200_000


async def test_a_failed_batch_stops_the_run_and_the_next_run_resumes(
    raw: Path, tmp_path: Path
) -> None:
    kb = FakeKb()
    kb.fail_calls = {2}
    state = tmp_path / "state"
    first = await run(raw, kb, state)
    assert first.status == "failed" and first.error
    assert first.added == 50 and len(kb.cards) == 50
    cursor = StateStore(state).load()
    assert cursor is not None and cursor.done == 50

    kb.fail_calls = set()
    calls_before = len(kb.calls)
    second = await run(raw, kb, state)
    assert second.status == "ok" and second.resumed_from == 50
    assert second.added == 70
    assert len(kb.calls) - calls_before == 2  # only the two batches that were left
    assert len(kb.cards) == 120
    assert StateStore(state).load() is None  # finished: the cursor is gone
    third = await run(raw, kb, state)
    assert third.added == 0 and third.resumed_from == 0


async def test_a_changed_input_ignores_a_stale_cursor(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    kb.fail_calls = {2}
    state = tmp_path / "state"
    await run(raw, kb, state)
    kb.fail_calls = set()
    changed = write_jsonl(tmp_path / "other.jsonl", list(synthetic_items(60, seed=99)))
    report = await run(changed, kb, state)
    assert report.resumed_from == 0 and report.status == "ok"


async def test_no_resume_flag_starts_over(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    kb.fail_calls = {2}
    state = tmp_path / "state"
    await run(raw, kb, state)
    kb.fail_calls = set()
    report = await run(raw, kb, state, resume=False)
    assert report.resumed_from == 0
    assert report.added == 70 and report.unchanged == 50


async def test_a_corrupt_state_file_is_ignored(raw: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "cursor.json").write_text("{{{ not json")
    report = await run(raw, FakeKb(), state)
    assert report.status == "ok" and report.added == 120


async def test_one_card_the_kb_refuses_is_dropped_and_the_rest_stored(
    raw: Path, tmp_path: Path
) -> None:
    items = [json.loads(line) for line in raw.read_text().splitlines()][:10]  # noqa: ASYNC240
    kb = FakeKb()
    bad = next(i for i in items if i["source"] == "reddit")
    kb.reject_urls = {normalise_url(bad["url"])}
    report = await run(write_jsonl(tmp_path / "r.jsonl", items), kb, tmp_path / "st")
    assert report.status == "ok"
    assert report.dropped == 1
    assert report.added == 9
    assert len(kb.cards) == 9


async def test_a_bad_token_fails_at_once_without_retrying_the_batches(
    raw: Path, tmp_path: Path
) -> None:
    kb = FakeKb()
    kb.unauthorized = True
    report = await run(raw, kb, tmp_path / "st")
    assert report.status == "failed"
    assert len(kb.calls) == 1


async def test_the_run_is_logged_in_ingestion_runs(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    state = tmp_path / "state"
    await run(raw, kb, state)
    await run(raw, kb, state)
    runs = StateStore(state).runs()
    assert [r["status"] for r in runs] == ["ok", "ok"]
    assert [r["added"] for r in runs] == [120, 0]
    assert runs[0]["started_at"] and runs[0]["run_id"] != runs[1]["run_id"]


async def test_the_source_is_committed_only_after_a_full_success(raw: Path, tmp_path: Path) -> None:
    class Spy(JsonlSource):
        commits = 0

        def commit(self) -> None:
            Spy.commits += 1

    kb = FakeKb()
    kb.fail_calls = {1}
    common: dict[str, Any] = {
        "state": StateStore(tmp_path / "st"),
        "dedupe_config": DedupeConfig(),
        "batch_size": 50,
        "today": TODAY,
        "now": now,
    }
    failed = await run_ingestion(Spy(raw), kb, **common)
    assert failed.status == "failed" and Spy.commits == 0
    kb.fail_calls = set()
    ok = await run_ingestion(Spy(raw), kb, **common)
    assert ok.status == "ok" and Spy.commits == 1


async def test_batch_size_one_and_the_cap(raw: Path, tmp_path: Path) -> None:
    kb = FakeKb()
    await run(raw, kb, tmp_path / "a", batch_size=1)
    assert len(kb.calls) == 120
    with pytest.raises(ValueError):
        await run(raw, FakeKb(), tmp_path / "b", batch_size=51)
    with pytest.raises(ValueError):
        await run(raw, FakeKb(), tmp_path / "c", batch_size=0)
