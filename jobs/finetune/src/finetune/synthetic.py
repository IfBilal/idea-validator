"""Synthetic examples for the end-to-end dry run (A1/A5). NEVER real data, never submitted.

The score is a deterministic function of marker words in the evidence, so a model can really
learn something and the loss curve is meaningful. Every example carries ``synthetic: true``;
the output directory is git-ignored.
"""

import random
from typing import Any

from common.schemas import DIMENSIONS

from .prompting import PromptFactory, make_example

_MARKERS = [
    ("rarely mentioned", 0.5),
    ("mentioned sometimes", 1.5),
    ("a recurring complaint", 2.5),
    ("a frequent complaint", 3.5),
    ("an urgent complaint", 4.5),
]
_TOPICS = ["notes app", "budget tracker", "lab scheduler", "recipe planner", "study timer"]


def _card_id(rng: random.Random, n: int) -> str:
    return f"card_{rng.getrandbits(32):08x}#c{n}"


def synthetic_examples(factory: PromptFactory, *, n: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    out: list[dict[str, Any]] = []
    for i in range(n):
        dim = DIMENSIONS[i % len(DIMENSIONS)]
        marker, score = _MARKERS[rng.randrange(len(_MARKERS))]
        topic = _TOPICS[rng.randrange(len(_TOPICS))]
        evidence = [
            (_card_id(rng, 0), f"SYNTHETIC: users of the {topic} say the problem is {marker}."),
            (_card_id(rng, 1), f"SYNTHETIC: another thread about the {topic}, {marker}."),
        ]
        out.append(
            make_example(
                factory,
                idea_id=f"syn{i // len(DIMENSIONS):04d}-{topic.replace(' ', '_')}-{i}",
                idea=f"SYNTHETIC idea {i}: a {topic} for students.",
                rubric_id=f"{dim}@v1",
                evidence=evidence,
                features={"n_sources": 2},
                score=score,
                confidence=round(0.5 + 0.1 * rng.randrange(4), 1),
                cited=[evidence[0][0]],
                reason=f"The evidence says the problem is {marker}.",
                extra_meta={"synthetic": True},
            )
        )
    return out
