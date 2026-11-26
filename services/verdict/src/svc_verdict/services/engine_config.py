"""M11 rules, loaded from the ConfigMap (``config/verdict.yaml``). Invalid rules refuse to load."""

import math
from pathlib import Path
from typing import Any, Self

from pydantic import Field, model_validator

from common.config import load_yaml_config
from common.schemas.base import Message
from common.schemas.types import DIMENSIONS, Dimension, Goal

WEIGHT_TOTAL = 100.0
WEIGHT_TOLERANCE = 1e-6


class Thresholds(Message):
    T_go: float
    T_no: float
    floor: float = Field(ge=0, le=5)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not 0 <= self.T_no <= self.T_go <= 100:
            raise ValueError("thresholds must satisfy 0 <= T_no <= T_go <= 100")
        return self


class ConfidenceRules(Message):
    min: float = Field(ge=0, le=1)
    w_dims: float = Field(ge=0, le=1)
    w_sources: float = Field(ge=0, le=1)
    sources_full: float = Field(gt=0)

    @model_validator(mode="after")
    def _weights_add_to_one(self) -> Self:
        if not math.isclose(self.w_dims + self.w_sources, 1.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("confidence.w_dims + confidence.w_sources must equal 1")
        return self


class GateRules(Message):
    min_sources: int = Field(ge=1)
    min_platforms: int = Field(ge=0)
    duplicate_diff_override: float = Field(ge=0, le=5)


class ChallengeRules(Message):
    score_step: float = Field(ge=0)
    score_max: float = Field(ge=0)
    conf_step: float = Field(ge=0)


class SensitivityRules(Message):
    weight_delta: float = Field(gt=0, lt=1)
    score_delta: float = Field(gt=0)


class WhatIfRules(Message):
    step: float = Field(gt=0)
    max_candidates: int = Field(ge=1)


class ScoringBlend(Message):
    alpha_model: float = Field(ge=0, le=1)


class EngineConfig(Message):
    engine_version: str
    weights_version: str
    thresholds_version: str
    rubric_set: str
    rubric_versions: dict[Dimension, str]
    weights: dict[Goal, dict[Dimension, float]]
    thresholds: Thresholds
    confidence: ConfidenceRules
    gates: GateRules
    challenge: ChallengeRules
    sensitivity: SensitivityRules
    whatif: WhatIfRules
    scoring_blend: ScoringBlend

    @model_validator(mode="after")
    def _weights_are_complete_and_add_to_100(self) -> Self:
        if set(self.weights) != {"startup", "portfolio", "course_project"}:
            raise ValueError("weights must define exactly: startup, portfolio, course_project")
        for goal, preset in self.weights.items():
            if set(preset) != set(DIMENSIONS):
                raise ValueError(f"weights.{goal} must define all six dimensions")
            if any(w < 0 for w in preset.values()):
                raise ValueError(f"weights.{goal} must not be negative")
            if sum(1 for w in preset.values() if w > 0) < 2:
                raise ValueError(f"weights.{goal} needs at least two non-zero weights")
            if not math.isclose(sum(preset.values()), WEIGHT_TOTAL, abs_tol=WEIGHT_TOLERANCE):
                raise ValueError(f"weights.{goal} must add to 100, got {sum(preset.values())}")
        return self

    @property
    def versions(self) -> dict[str, str]:
        return {
            "engine": self.engine_version,
            "rubrics": self.rubric_set,
            "profile_weights": self.weights_version,
        }

    def weights_for(self, goal: str) -> dict[str, float]:
        return {d: self.weights[goal][d] for d in DIMENSIONS}  # type: ignore[index]


def parse_engine_config(data: dict[str, Any]) -> EngineConfig:
    return EngineConfig.model_validate(data)


def load_engine_config(config_dir: Path | None = None) -> EngineConfig:
    """Read ``<config_dir>/verdict.yaml`` (the mounted ConfigMap) and validate it."""
    return parse_engine_config(load_yaml_config("verdict", config_dir=config_dir))
