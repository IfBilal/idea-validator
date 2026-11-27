import json
from pathlib import Path

import pytest
from ingest_fakes import FakeKb
from synthetic import synthetic_items, write_jsonl

from ingestion.cli import main
from ingestion.client import HttpKbClient


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KB_URL", "http://kb:8000")
    monkeypatch.setenv("INTERNAL_TOKEN", "t")
    monkeypatch.setenv("INGESTION_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_run_snapshot_restore_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kb = FakeKb()
    raw = write_jsonl(tmp_path / "raw.jsonl", list(synthetic_items(30, seed=1)))
    assert main(["run", "--input", str(raw), "--date", "2026-03-01"], kb_factory=lambda s: kb) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["added"] == 30 and out["status"] == "ok"
    assert main(["run", "--input", str(raw)], kb_factory=lambda s: kb) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["added"] == 0

    snaps = tmp_path / "snaps"
    assert (
        main(["snapshot", "--out-dir", str(snaps), "--date", "2026-03-01"], kb_factory=lambda s: kb)
        == 0
    )
    assert (snaps / "cards-20260301.jsonl.gz").exists()
    capsys.readouterr()

    fresh = FakeKb()
    assert (
        main(["restore", str(snaps)], kb_factory=lambda s: fresh) == 0
    )  # a directory: newest file
    assert len(fresh.cards) == 30
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["added"] == 30


def test_a_failed_run_exits_one_and_the_rerun_finishes(tmp_path: Path) -> None:
    kb = FakeKb()
    kb.fail_calls = {1}
    raw = write_jsonl(tmp_path / "raw.jsonl", list(synthetic_items(10, seed=1)))
    assert main(["run", "--input", str(raw)], kb_factory=lambda s: kb) == 1
    kb.fail_calls = set()
    assert main(["run", "--input", str(raw)], kb_factory=lambda s: kb) == 0
    assert len(kb.cards) == 10


def test_missing_configuration_and_bad_usage_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = write_jsonl(tmp_path / "raw.jsonl", list(synthetic_items(2)))
    assert main([]) == 2
    assert main(["bogus"]) == 2
    assert main(["run"]) == 2  # no --input and no DATABASE_URL
    assert (
        main(["run", "--input", str(tmp_path / "missing.jsonl")], kb_factory=lambda s: FakeKb())
        == 2
    )
    assert (
        main(["run", "--input", str(raw), "--date", "not-a-date"], kb_factory=lambda s: FakeKb())
        == 2
    )
    monkeypatch.delenv("KB_URL")
    assert main(["run", "--input", str(raw)]) == 2


def test_restore_of_nothing_exits_three(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["restore", str(empty)], kb_factory=lambda s: FakeKb()) == 3


def test_snapshot_of_an_empty_kb_exits_three(tmp_path: Path) -> None:
    assert main(["snapshot", "--out-dir", str(tmp_path / "s")], kb_factory=lambda s: FakeKb()) == 3


def test_the_default_client_is_the_http_client() -> None:
    from ingestion.cli import make_kb
    from ingestion.config import Settings

    assert isinstance(make_kb(Settings()), HttpKbClient)
