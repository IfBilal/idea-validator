"""Fails if `.gitignore` silently hides source files (a bare `models/` once hid 14 packages)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE_SUFFIXES = (".py", ".pyi", ".tmpl", ".yaml", ".yml", ".toml", ".sql", ".ini", ".mako", ".sh")
NOT_SOURCE = (
    ".venv/", "node_modules/", "__pycache__/", ".mypy_cache/", ".ruff_cache/",
    ".pytest_cache/", ".hypothesis/", ".import_linter_cache/", ".claude/settings.local",
)  # fmt: skip


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout"
)
def test_no_source_file_is_hidden_by_gitignore() -> None:
    out = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    hidden = [
        p for p in out if p.endswith(SOURCE_SUFFIXES) and not any(skip in p for skip in NOT_SOURCE)
    ]
    assert hidden == [], f"these look like source files but are ignored by .gitignore: {hidden}"
