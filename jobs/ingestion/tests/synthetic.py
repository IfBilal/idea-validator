"""SYNTHETIC test fixtures. Never real data, never a snapshot, never imported by the job itself.

Every item lives on the reserved ``.invalid`` TLD and carries the ``synthetic`` topic, so a card made
from it is recognisable at a glance and can never be mistaken for scraped evidence. This module is
under ``tests/`` on purpose: ``.dockerignore`` keeps it out of the image, and nothing in the job
imports it.
"""

import json
import random
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SOURCES = ("hackernews", "github", "reddit", "producthunt")
_SYLLABLES = ("ka", "lo", "mi", "ren", "tu", "va", "zo", "pe", "xi", "dar", "bu", "nel")


def _word(rng: random.Random) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 4)))


def synthetic_items(
    n: int,
    *,
    seed: int = 0,
    sources: tuple[str, ...] = SOURCES,
    start: date = date(2025, 1, 1),
) -> Iterator[dict[str, Any]]:
    """``n`` distinct raw items, round-robin over ``sources``, fully determined by ``seed``."""
    rng = random.Random(seed)  # noqa: S311
    for i in range(n):
        source = sources[i % len(sources)]
        title_words = " ".join(_word(rng) for _ in range(6))
        body = ". ".join(" ".join(_word(rng) for _ in range(9)) for _ in range(3)) + "."
        if source == "hackernews":
            title = f"Show HN: {title_words} {i}"
        elif source == "github":
            title = f"synthetic-org/{_word(rng)}-{i}"
        else:
            title = f"{title_words} {i}"
        yield {
            "source": source,
            "url": f"https://item-{i}.synthetic.invalid/{source}/{i}?utm_source=fixture",
            "title": title,
            "text": body,
            "created_at": (start + timedelta(days=i % 300)).isoformat(),
            "score": rng.randint(0, 500),
            "metadata": {"topics": ["synthetic", f"topic-{i % 17}"], "author": f"user{i}"},
        }


def write_jsonl(path: Path, items: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return path
