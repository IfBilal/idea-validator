import json
import multiprocessing
import threading
from pathlib import Path

import pytest

from svc_agentops.repositories.files import JsonlTraceFiles, UnsafeRunId


def store(tmp_path: Path) -> JsonlTraceFiles:
    files = JsonlTraceFiles(tmp_path / "t")
    files.prepare()
    return files


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "a\\b", "", "x" * 65, "run.1", "a\x00b", "~"])
def test_unsafe_run_ids_never_become_paths(tmp_path: Path, bad: str) -> None:
    files = store(tmp_path)
    with pytest.raises(UnsafeRunId):
        files.path_for(bad)
    with pytest.raises(UnsafeRunId):
        files.append(bad, [("i", {})])
    with pytest.raises(UnsafeRunId):
        files.read(bad, 10)
    assert not (tmp_path / "x").exists()


def test_append_then_read_roundtrip_in_order(tmp_path: Path) -> None:
    files = store(tmp_path)
    files.append("r1", [("a", {"n": 1}), ("b", {"n": 2})])
    files.append("r1", [("c", {"n": 3})])
    rows, truncated = files.read("r1", 10)
    assert [r[0] for r in rows] == ["a", "b", "c"]
    assert not truncated
    assert files.exists("r1")
    assert not files.exists("nope")
    assert files.read("nope", 10) == ([], False)


def test_reads_deduplicate_by_id_skip_torn_lines_and_honour_the_limit(tmp_path: Path) -> None:
    files = store(tmp_path)
    files.append("r1", [("a", {"n": 1})])
    files.append("r1", [("a", {"n": 1}), ("b", {"n": 2}), ("c", {"n": 3})])
    with files.path_for("r1").open("a") as handle:
        handle.write('{"id": "torn", "record": {\nnot json\n[1,2]\n')
    rows, truncated = files.read("r1", 10)
    assert [r[0] for r in rows] == ["a", "b", "c"]
    assert not truncated
    rows, truncated = files.read("r1", 2)
    assert len(rows) == 2
    assert truncated


def test_a_symlink_is_never_followed(tmp_path: Path) -> None:
    files = store(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    files.path_for("evil").symlink_to(outside)
    with pytest.raises(UnsafeRunId):
        files.append("evil", [("a", {})])
    assert outside.read_text() == ""


def test_concurrent_threads_never_interleave_lines(tmp_path: Path) -> None:
    files = store(tmp_path)
    big = "y" * 20_000

    def writer(n: int) -> None:
        for i in range(20):
            files.append("r1", [(f"{n}-{i}-{j}", {"pad": big}) for j in range(3)])

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = files.path_for("r1").read_text().splitlines()
    assert len(lines) == 8 * 20 * 3
    assert all(json.loads(line)["record"]["pad"] == big for line in lines)


def _proc_writer(directory: str, n: int) -> None:
    files = JsonlTraceFiles(Path(directory))
    for i in range(15):
        files.append("r1", [(f"{n}-{i}", {"pad": "z" * 50_000})])


def test_concurrent_processes_never_interleave_lines(tmp_path: Path) -> None:
    files = store(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_proc_writer, args=(str(tmp_path / "t"), n)) for n in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    rows, _ = files.read("r1", 1000)
    assert len(rows) == 60
