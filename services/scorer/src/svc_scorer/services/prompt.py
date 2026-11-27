"""Build the scoring prompt from ``prompts/scorer@v1.md`` and the rubric anchors.

Evidence is untrusted text copied from the internet. It is HTML-escaped (so it cannot close or
open a delimiter) and wrapped in ``<evidence id=...>`` tags (docs/harness/09 §1, CLAUDE.md rule 5).
"""

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from common.errors import NotFound, ValidationFailed
from common.schemas import DIMENSIONS, ScoreRequest

PROMPT_FILE = "scorer@v1.md"
PROFILE_DIMENSIONS = frozenset({"feasibility", "learning"})
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class RubricAnchors:
    rubric_id: str
    dimension: str
    anchors: dict[int, str]


def dimension_of(rubric_id: str) -> str:
    dimension = rubric_id.split("@", 1)[0]
    if dimension not in DIMENSIONS:
        raise ValidationFailed(f"unknown dimension in rubric_id {rubric_id!r}")
    return dimension


def load_anchors(rubrics_dir: Path, rubric_id: str) -> RubricAnchors:
    dimension = dimension_of(rubric_id)
    path = rubrics_dir / f"{rubric_id}.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise NotFound(f"rubric {rubric_id} not found") from exc
    except yaml.YAMLError as exc:
        raise ValidationFailed(f"rubric {rubric_id} is not valid YAML") from exc
    raw = data.get("anchors") if isinstance(data, dict) else None
    if not isinstance(raw, dict) or data.get("dimension") != dimension:
        raise ValidationFailed(f"rubric {rubric_id} is malformed")
    anchors = {int(k): str(v) for k, v in raw.items() if str(k).isdigit()}
    if sorted(anchors) != [0, 1, 2, 3, 4, 5]:
        raise ValidationFailed(f"rubric {rubric_id} must define anchors 0..5")
    return RubricAnchors(rubric_id, dimension, anchors)


def _fill(template: str, values: dict[str, str]) -> str:
    """Single-pass substitution so substituted data is never re-expanded."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


class PromptBuilder:
    def __init__(self, rubrics_dir: Path, prompts_dir: Path) -> None:
        self._rubrics_dir = rubrics_dir
        text = (prompts_dir / PROMPT_FILE).read_text()
        body = text.split("\n---\n", 1)[1] if text.startswith("---") else text
        system, _, user = body.partition("## USER")
        self._system = system.replace("## SYSTEM", "", 1).strip()
        self._user = user.strip()

    def build(self, request: ScoreRequest) -> tuple[str, str]:
        rubric = load_anchors(self._rubrics_dir, request.rubric_id)
        anchors = "\n".join(f"{level}: {rubric.anchors[level]}" for level in range(5, -1, -1))
        system = _fill(
            self._system,
            {"dimension": rubric.dimension, "rubric_id": rubric.rubric_id, "anchors": anchors},
        )
        blocks = "\n".join(
            f'<evidence id="{e.evidence_id}">{html.escape(e.quote, quote=False)}</evidence>'
            for e in request.evidence
        )
        profile_block = ""
        if request.profile is not None and rubric.dimension in PROFILE_DIMENSIONS:
            profile_json = request.profile.model_dump_json(exclude={"user_id", "version"})
            profile_block = f"PROFILE: {profile_json}"
        template = self._user if profile_block else self._user.replace("{profile_block}\n", "")
        user = _fill(
            template,
            {
                "idea_text": request.idea,
                "profile_block": profile_block,
                "features_json": json.dumps(request.features, sort_keys=True, default=str),
                "evidence_blocks": blocks,
            },
        )
        return system, user
