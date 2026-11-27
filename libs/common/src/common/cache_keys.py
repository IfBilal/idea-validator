"""Cache keys (CLAUDE.md rule 6): every verdict key carries every version that can matter.

Changing the idea text, the idea version, the profile version, the rubric set, the weights version
or the engine version produces a different key, so an old verdict can never survive a rule change.
"""

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")


def idea_hash(text: str) -> str:
    """Stable short hash of the idea text, ignoring case and whitespace differences."""
    normalised = _WHITESPACE.sub(" ", text).strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


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
    """The part after ``verdict:`` in ``verdict:{idea}:{version}:{hash}:p{}:r{}:w{}:e{}``."""
    return (
        f"{idea_id}:{idea_version}:{idea_hash(idea_text)}"
        f":p{profile_version}:r{rubrics_version}:w{weights_version}:e{engine_version}"
    )
