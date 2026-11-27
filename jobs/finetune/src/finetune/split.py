"""Grouped split by idea + overlap assertion against the labelled verdict set.

Rows of one idea always land in the same part (docs/harness/11 §1.4). The text comparison runs
on bounded, normalised text so hostile or huge scraped strings cannot stall the build.
"""

import random
import re
import unicodedata
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from typing import Any

SIMILARITY_LIMIT = 0.9
MAX_TEXT = 4_000  # same bound as the ScoreRequest idea field
_NON_WORD = re.compile(r"[^\w\s]+")
_SPACE = re.compile(r"\s+")


class OverlapError(AssertionError):
    """A training idea also appears in the labelled (human verdict) set."""


def normalise(text: str) -> str:
    """Lowercase, drop punctuation, collapse spaces. Bounded before any regex runs."""
    clipped = text[:MAX_TEXT]
    folded = unicodedata.normalize("NFKC", clipped)[: MAX_TEXT * 2]
    return _SPACE.sub(" ", _NON_WORD.sub(" ", folded.lower())).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


def grouped_split(
    rows: Iterable[Mapping[str, Any]],
    *,
    test_ideas: set[str],
    val_fraction: float,
    seed: int,
) -> dict[str, list[Mapping[str, Any]]]:
    """Split rows into train/val by ``idea_id``; ``test_ideas`` are excluded from both."""
    items = [r for r in rows if r["idea_id"] not in test_ideas]
    ideas = sorted({r["idea_id"] for r in items})
    rng = random.Random(seed)  # noqa: S311 - deterministic split, not security
    rng.shuffle(ideas)
    n_val = round(len(ideas) * val_fraction) if len(ideas) > 1 else 0
    val_ids = set(ideas[:n_val])
    return {
        "train": [r for r in items if r["idea_id"] not in val_ids],
        "val": [r for r in items if r["idea_id"] in val_ids],
    }


def assert_no_overlap(
    rows: Iterable[Mapping[str, Any]],
    labelled: Iterable[Mapping[str, Any]],
    *,
    require_nonempty: bool = False,
) -> None:
    """Fail the build if any idea matches a labelled one by id or by text similarity >= 0.9."""
    lab = list(labelled)
    if require_nonempty and not lab:
        raise OverlapError("the labelled set is empty: refusing to claim zero overlap")
    lab_ids = {str(item.get("id")) for item in lab}
    lab_texts = [(str(item.get("id")), normalise(str(item.get("idea_text", "")))) for item in lab]
    seen: dict[str, str] = {}
    for row in rows:
        idea_id = str(row["idea_id"])
        text = row.get("idea_text")
        if not isinstance(text, str) or not text.strip():
            raise OverlapError(f"idea {idea_id} has no text: cannot prove it is disjoint")
        seen.setdefault(idea_id, text)
    for idea_id, text in seen.items():
        if idea_id in lab_ids:
            raise OverlapError(f"idea id {idea_id} is in the labelled set")
        norm = normalise(text)
        for lab_id, lab_norm in lab_texts:
            if (
                lab_norm
                and SequenceMatcher(None, norm, lab_norm, autojunk=False).ratio()
                >= SIMILARITY_LIMIT
            ):
                raise OverlapError(f"idea {idea_id} is similar (>= 0.9) to labelled item {lab_id}")
