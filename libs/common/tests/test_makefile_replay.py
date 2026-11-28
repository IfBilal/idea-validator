"""`make replay` validates RUN and runs the replay exactly once (fake uv/kubectl on PATH)."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def run_make(tmp_path: Path, run: str, **env: str) -> tuple[int, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("uv", "kubectl"):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    full_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUN": run, **env}
    full_env.pop("TRACE_DIR", None) if "TRACE_DIR" not in env else None
    proc = subprocess.run(  # noqa: S603
        ["make", "-C", str(ROOT), "replay"],  # noqa: S607
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc.returncode, calls


def test_a_valid_run_id_is_replayed_exactly_once_locally(tmp_path: Path) -> None:
    code, calls = run_make(tmp_path, "run_abc-1", TRACE_DIR="/some/dir")
    assert code == 0
    assert len(calls) == 1
    assert calls[0].startswith("uv run")
    assert calls[0].endswith("-- run_abc-1")


def test_a_valid_run_id_is_replayed_exactly_once_in_the_cluster(tmp_path: Path) -> None:
    code, calls = run_make(tmp_path, "run_abc-1")
    assert code == 0
    assert len(calls) == 1
    assert calls[0].startswith("kubectl")
    assert calls[0].endswith("-- run_abc-1")


@pytest.mark.parametrize(
    "bad", ["", "run 1", "x;echo hi", "$(id)", "../x", "a" * 65, "run.1", "`id`"]
)
def test_an_invalid_run_id_never_reaches_uv_or_kubectl(tmp_path: Path, bad: str) -> None:
    code, calls = run_make(tmp_path, bad, TRACE_DIR="/some/dir")
    assert code == 2
    assert calls == []
