"""Drift reports as small JSON files (``drift-<UTC stamp>.json``), newest wins, old ones pruned."""

import os
import re
from datetime import UTC
from pathlib import Path

from ..models.drift import DriftReport

_NAME = re.compile(r"drift-\d{8}T\d{6}Z\.json")


class DriftStore:
    def __init__(self, directory: Path, keep: int = 60) -> None:
        self._dir = directory
        self._keep = keep

    def _files(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted(p for p in self._dir.iterdir() if _NAME.fullmatch(p.name) and p.is_file())

    def save(self, report: DriftReport) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        name = f"drift-{report.generated_at.astimezone(UTC):%Y%m%dT%H%M%SZ}.json"
        target = self._dir / name
        tmp = self._dir / f".{name}.tmp"
        tmp.write_text(report.model_dump_json(indent=2))
        os.replace(tmp, target)  # readers never see a half-written report
        for old in self._files()[: -self._keep]:
            old.unlink(missing_ok=True)
        return name

    def latest(self) -> DriftReport | None:
        for path in reversed(self._files()):
            try:
                return DriftReport.model_validate_json(path.read_text())
            except (OSError, ValueError):
                continue  # a corrupt file never hides an older good report
        return None
