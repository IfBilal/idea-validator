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


def test_a_torn_last_line_is_closed_before_the_next_append(tmp_path: Path) -> None:
    files = store(tmp_path)
    files.append("r1", [("a", {"n": 1})])
    with files.path_for("r1").open("ab") as handle:
        handle.write(b'{"id": "torn", "record": {"n"')  # a crash mid-write: no newline
    files.append("r1", [("b", {"n": 2})])
    rows, _ = files.read("r1", 10)
    assert [r[0] for r in rows] == ["a", "b"]  # the good record is not glued to the torn one


def test_the_byte_cap_is_enforced_under_the_lock(tmp_path: Path) -> None:
    from svc_agentops.repositories.files import RunFull

    files = store(tmp_path)
    files.append("r1", [("a", {"pad": "x" * 100})], 400)
    with pytest.raises(RunFull):
        files.append("r1", [("b", {"pad": "y" * 400})], 400)
    assert [r[0] for r in files.read("r1", 10)[0]] == ["a"]


def test_listing_and_deleting_runs_only_touches_valid_names(tmp_path: Path) -> None:
    files = store(tmp_path)
    files.append("r1", [("a", {})])
    (tmp_path / "t" / "not a run.jsonl").write_text("x")
    (tmp_path / "t" / "r2.txt").write_text("x")
    assert files.run_ids() == ["r1"]
    assert files.mtime("r1") is not None
    assert files.mtime("nope") is None
    assert files.delete_if_older("r1", 9e12) is True
    assert files.delete_if_older("r1", 9e12) is False  # idempotent
    assert files.run_ids() == []


def test_ids_already_in_the_file_are_not_written_again_and_do_not_burn_the_cap(
    tmp_path: Path,
) -> None:
    files = store(tmp_path)
    batch = [(f"id{i}", {"pad": "x" * 50}) for i in range(5)]
    files.append("r1", batch, 1000)
    size = files.path_for("r1").stat().st_size
    for _ in range(50):  # retries of the same batch must not grow the file or hit the cap
        files.append("r1", batch, 1000)
    assert files.path_for("r1").stat().st_size == size
    assert len(files.path_for("r1").read_text().splitlines()) == 5


def test_20_concurrent_identical_batches_leave_exactly_the_batch(tmp_path: Path) -> None:
    files = store(tmp_path)
    batch = [(f"id{i}", {"n": i}) for i in range(30)]
    threads = [
        threading.Thread(target=files.append, args=("r1", batch, 100_000)) for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(files.path_for("r1").read_text().splitlines()) == 30


def test_10_concurrent_batches_of_10_against_a_cap_of_50_store_exactly_50(tmp_path: Path) -> None:
    from svc_agentops.repositories.files import RunFull

    files = store(tmp_path)
    outcomes: list[str] = []

    def writer(n: int) -> None:
        try:
            files.append("r1", [(f"{n}-{i}", {"n": i}) for i in range(10)], 10_000_000, 50)
            outcomes.append("ok")
        except RunFull:
            outcomes.append("full")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 5
    assert outcomes.count("full") == 5
    assert len(files.read("r1", 1000)[0]) == 50


def test_a_torn_line_with_an_id_does_not_count_as_a_stored_record(tmp_path: Path) -> None:
    files = store(tmp_path)
    with files.path_for("r1").open("wb") as handle:
        handle.write(b'{"id":"a","record":{"n"')  # crash mid-write
    files.append("r1", [("a", {"n": 1})], 10_000)
    assert [r[0] for r in files.read("r1", 10)[0]] == ["a"]  # the retry really stored it


def test_delete_if_older_takes_the_lock_and_rechecks_the_age(tmp_path: Path) -> None:
    import fcntl
    import os
    import time

    files = store(tmp_path)
    files.append("r1", [("a", {})])
    now = time.time()
    assert files.delete_if_older("r1", now - 3600) is False  # written just now: kept
    assert files.exists("r1")
    old = now - 40 * 86400
    os.utime(files.path_for("r1"), (old, old))

    held = os.open(files.path_for("r1"), os.O_RDWR)
    fcntl.flock(held, fcntl.LOCK_EX)  # an append is in flight
    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(files.delete_if_older("r1", now - 86400))
    )
    worker.start()
    time.sleep(0.3)
    assert result == []  # purge waits for the lock
    assert files.exists("r1")
    os.write(held, b"")
    os.utime(files.path_for("r1"), (now, now))  # the append finished and refreshed the file
    os.close(held)
    worker.join(5)
    assert result == [False]  # re-checked under the lock: now recent, so kept
    assert files.exists("r1")
    os.utime(files.path_for("r1"), (old, old))
    assert files.delete_if_older("r1", now - 86400) is True
    assert not files.exists("r1")
    assert files.delete_if_older("r1", now) is False  # already gone
