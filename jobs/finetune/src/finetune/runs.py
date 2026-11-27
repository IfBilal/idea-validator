"""``runs/<run>.json``: the record of one training run (settings, hardware, curve, wall time)."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class RunRecord(BaseModel):
    run_id: str
    base_model: str
    settings: dict[str, Any]
    hardware: dict[str, Any]
    versions: dict[str, str]
    metrics: dict[str, Any]
    wall_time_s: float
    synthetic: bool = False
    loss_curve: list[dict[str, Any]] = []
    notes: list[str] = []


def write_run(record: RunRecord, directory: Path | str) -> Path:
    """Write ``<run_id>.json``; refuse to overwrite an earlier run record."""
    target = Path(directory) / f"{record.run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as handle:
        json.dump(record.model_dump(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return target
