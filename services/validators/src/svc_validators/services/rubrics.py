"""Strict rubric loader: exact pinned versions, anchors 0..5, parseable and total priors."""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from common.schemas import DIMENSIONS

from ..models.rubric import Rubric
from .expr import ExprError, compile_expr, names


class RubricError(Exception):
    """A rubric file is missing or does not satisfy the strict schema."""


def validate_rules(rubric: Rubric) -> None:
    declared = set(rubric.features)
    for rule in rubric.feature_prior:
        try:
            used = names(compile_expr(rule.when))
        except ExprError as exc:
            raise RubricError(f"{rubric.id}: bad rule {rule.when!r}: {exc}") from exc
        if unknown := used - declared:
            raise RubricError(f"{rubric.id}: rule {rule.when!r} uses undeclared {sorted(unknown)}")
    if rubric.feature_prior[-1].when.strip().lower() != "true":
        raise RubricError(f"{rubric.id}: the last feature_prior rule must be 'true'")


def load_rubric(rubrics_dir: Path, dimension: str, version: int) -> Rubric:
    """Load exactly ``<dimension>@v<version>.yaml``; never "the latest"."""
    rubric_id = f"{dimension}@v{version}"
    path = rubrics_dir / f"{rubric_id}.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise RubricError(f"rubric {rubric_id} not found in {rubrics_dir}") from exc
    except yaml.YAMLError as exc:
        raise RubricError(f"rubric {rubric_id} is not valid YAML") from exc
    if not isinstance(data, dict):
        raise RubricError(f"rubric {rubric_id} must be a mapping")
    try:
        rubric = Rubric.model_validate(data)
    except ValidationError as exc:
        raise RubricError(f"rubric {rubric_id} is invalid: {exc.errors()[0]['msg']}") from exc
    if rubric.id != rubric_id or rubric.dimension != dimension or rubric.version != version:
        raise RubricError(f"rubric file {rubric_id} declares {rubric.id}")
    validate_rules(rubric)
    return rubric


def load_rubrics(rubrics_dir: Path, versions: Mapping[str, int]) -> dict[str, Rubric]:
    """Load all six dimensions at their pinned versions."""
    if unknown := set(versions) - set(DIMENSIONS):
        raise RubricError(f"pins for unknown dimensions: {sorted(unknown)}")
    missing = [d for d in DIMENSIONS if d not in versions]
    if missing:
        raise RubricError(f"no rubric version pinned for: {', '.join(missing)}")
    return {d: load_rubric(rubrics_dir, d, versions[d]) for d in DIMENSIONS}
