"""Build prompts with the PRODUCTION prompt builder (09 §7, prompts/scorer@v1.md).

Training, inference, the base/fine-tuned/hosted comparison and draft labelling all go through
``PromptFactory`` so every model sees byte-identical prompts (fair comparison, M10-11).
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from svc_scorer.services.prompt import PromptBuilder, dimension_of

from common.schemas import ScoreEvidence, ScoreRequest, StudentProfile

Evidence = Sequence[tuple[str, str]]


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "rubrics").is_dir() and (parent / "prompts").is_dir():
            return parent
    raise FileNotFoundError("repository root (rubrics/ and prompts/) not found")


class PromptFactory:
    def __init__(self, rubrics_dir: Path, prompts_dir: Path) -> None:
        self._builder = PromptBuilder(rubrics_dir, prompts_dir)

    def build(
        self,
        rubric_id: str,
        idea: str,
        evidence: Evidence,
        features: dict[str, Any],
        profile: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        request = ScoreRequest(
            rubric_id=rubric_id,
            idea=idea,
            profile=StudentProfile.model_validate(profile) if profile else None,
            evidence=[ScoreEvidence(evidence_id=i, quote=q) for i, q in evidence],
            features=features,
            run_id="finetune",
        )
        return self._builder.build(request)


def target_json(score: float | None, confidence: float, cited: Sequence[str], reason: str) -> str:
    return json.dumps(
        {
            "score": score,
            "confidence": confidence,
            "evidence_ids": list(cited),
            "reason": reason,
        },
        ensure_ascii=False,
    )


def make_example(
    factory: PromptFactory,
    *,
    idea_id: str,
    idea: str,
    rubric_id: str,
    evidence: Evidence,
    features: dict[str, Any],
    score: float | None,
    confidence: float,
    cited: Sequence[str],
    reason: str,
    profile: dict[str, Any] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system, user = factory.build(rubric_id, idea, evidence, features, profile)
    meta: dict[str, Any] = {
        "idea_id": idea_id,
        "idea_text": idea,
        "dimension": dimension_of(rubric_id),
        "level": score,
        "hand_checked": False,
        "rubric_version": rubric_id,
        "evidence_ids": [i for i, _ in evidence],
    }
    meta.update(extra_meta or {})
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": target_json(score, confidence, cited, reason)},
        ],
        "meta": meta,
    }
