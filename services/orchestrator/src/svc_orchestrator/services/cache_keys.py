"""Cache and memo keys (CLAUDE.md rule 6, docs/harness/04 §3, 07 §7).

A verdict cache key carries every version that can change the result, so a profile, rubric,
weights or engine bump can never serve a stale verdict.
"""

import hashlib
import json
from typing import Any


def canonical_hash(value: Any) -> str:
    """sha256 over canonical JSON: key order never matters, list order does."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


def idea_hash(idea_text: str) -> str:
    """Whitespace-insensitive hash of the idea text (16 hex chars)."""
    return hashlib.sha256(" ".join(idea_text.split()).encode()).hexdigest()[:16]


def memo_key(step: str, inputs: Any) -> str:
    """Key of a memoised step output: the step name plus a hash of everything it read."""
    return f"{step}:{canonical_hash(inputs)[:16]}"


def verdict_cache_key(
    *,
    idea_id: str,
    idea_version: int,
    idea_text: str,
    profile_version: int,
    rubrics_version: str,
    weights_version: str,
    engine_version: str,
) -> str:
    for name, number in (("idea_version", idea_version), ("profile_version", profile_version)):
        if number < 1:
            raise ValueError(f"{name} must be >= 1")
    for name, text in (
        ("idea_id", idea_id),
        ("rubrics_version", rubrics_version),
        ("weights_version", weights_version),
        ("engine_version", engine_version),
    ):
        if not text:
            raise ValueError(f"{name} must not be empty")
    return (
        f"verdict:{idea_id}:{idea_version}:{idea_hash(idea_text)}"
        f":p{profile_version}:r{rubrics_version}:w{weights_version}:e{engine_version}"
    )
