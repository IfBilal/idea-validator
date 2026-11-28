"""The labelled verdict set (eval/labelled/README.md): one JSON object per line.

Loading is strict: a malformed set would silently change what is measured, so every problem is
reported (with its line number) and nothing is guessed. ``human_verdict`` must be one of the four
M11 verdicts; ids are unique.
"""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.metrics import LABELS

PROFILE_FIELDS = ("skills", "hours_per_week", "weeks_available", "team_size", "compute", "goal")
OPTIONAL_PROFILE_FIELDS = ("interests",)
ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")  # fullmatch: a trailing newline does not pass
MAX_IDEA_CHARS = 4000  # the gateway refuses longer idea text
MAX_EFFORT_HOURS = 10_000  # M3's accepted range for estimated_effort_hours


class LabelledSetError(ValueError):
    """The labelled set cannot be used; the message names the file, line and problem."""


@dataclass(frozen=True)
class LabelledItem:
    id: str
    idea_text: str
    profile: dict[str, Any]
    human_verdict: str
    category: str | None = None
    poisoned: bool = False
    injection_canary: str | None = None  # text planted by the poisoned item that must not leak
    injection_forces: str | None = None  # the verdict the planted text demands
    estimated_effort_hours: float | None = None  # the only answer given to the effort question

    def profile_body(self) -> dict[str, Any]:
        """The ``PUT /profile`` body: the labelled profile without ``user_id`` and ``version``."""
        keys = (*PROFILE_FIELDS, *OPTIONAL_PROFILE_FIELDS)
        body = {k: self.profile[k] for k in keys if k in self.profile}
        body.setdefault("interests", [])
        return body


def _fail(path: Path, line_no: int, message: str) -> LabelledSetError:
    return LabelledSetError(f"{path}:{line_no}: {message}")


def _optional_str(raw: dict[str, Any], key: str, path: Path, line_no: int) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(path, line_no, f"{key!r} must be a non-empty string")
    return value


def _parse(raw: Any, path: Path, line_no: int) -> LabelledItem:
    if not isinstance(raw, dict):
        raise _fail(path, line_no, "each line must be a JSON object")
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
        raise _fail(path, line_no, "'id' must match [A-Za-z0-9._-]{1,64}")
    text = raw.get("idea_text")
    if not isinstance(text, str) or not text.strip():
        raise _fail(path, line_no, f"{item_id}: 'idea_text' must be a non-empty string")
    if len(text) > MAX_IDEA_CHARS:
        raise _fail(path, line_no, f"{item_id}: 'idea_text' is longer than {MAX_IDEA_CHARS}")
    verdict = raw.get("human_verdict")
    if verdict not in LABELS:
        raise _fail(path, line_no, f"{item_id}: 'human_verdict' must be one of {list(LABELS)}")
    profile = raw.get("profile")
    if not isinstance(profile, dict):
        raise _fail(path, line_no, f"{item_id}: 'profile' must be an object")
    missing = [k for k in PROFILE_FIELDS if k not in profile]
    if missing:
        raise _fail(path, line_no, f"{item_id}: profile is missing {missing}")
    poisoned = raw.get("poisoned", False)
    if not isinstance(poisoned, bool):
        raise _fail(path, line_no, f"{item_id}: 'poisoned' must be true or false")
    forces = _optional_str(raw, "injection_forces", path, line_no)
    if forces is not None and forces not in LABELS:
        raise _fail(path, line_no, f"{item_id}: 'injection_forces' must be one of {list(LABELS)}")
    effort = raw.get("estimated_effort_hours")
    if effort is not None:
        ok = isinstance(effort, int | float) and not isinstance(effort, bool)
        if not ok or not math.isfinite(effort) or not 0 <= effort <= MAX_EFFORT_HOURS:
            raise _fail(path, line_no, f"{item_id}: 'estimated_effort_hours' must be 0..10000")
    return LabelledItem(
        id=item_id,
        idea_text=text,
        profile=profile,
        human_verdict=verdict,
        category=_optional_str(raw, "category", path, line_no),
        poisoned=poisoned,
        injection_canary=_optional_str(raw, "injection_canary", path, line_no),
        injection_forces=forces,
        estimated_effort_hours=float(effort) if effort is not None else None,
    )


def load_labelled(path: Path) -> list[LabelledItem]:
    """Read the set in file order. Raises ``LabelledSetError`` when it is missing, empty or bad."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LabelledSetError(f"{path}: labelled set not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelledSetError(f"{path}: cannot be read ({type(exc).__name__})") from None
    items: list[LabelledItem] = []
    seen: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raise _fail(path, line_no, "not valid JSON") from None
        item = _parse(raw, path, line_no)
        if item.id in seen:
            raise _fail(path, line_no, f"duplicate id {item.id!r}")
        seen.add(item.id)
        items.append(item)
    if not items:
        raise LabelledSetError(f"{path}: labelled set is empty")
    return items
