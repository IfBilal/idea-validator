from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from common.schemas.types import DIMENSIONS
from svc_verdict.models.inputs import EngineInput
from svc_verdict.services.engine_config import EngineConfig, parse_engine_config

CONFIG_FILE = Path(__file__).resolve().parents[3] / "config" / "verdict.yaml"
GOOD = {d: 4.0 for d in DIMENSIONS}
WORKED_EXAMPLE = {
    "demand_pain": 3.5,
    "competition": 2.0,
    "differentiation": 3.0,
    "feasibility": 4.0,
    "risk": 3.5,
    "learning": 4.5,
}
WORKED_EXAMPLE_CONF = {
    "demand_pain": 0.8,
    "competition": 0.6,
    "differentiation": 0.7,
    "feasibility": 0.8,
    "risk": 0.75,
    "learning": 0.85,
}


@pytest.fixture(scope="session")
def cfg() -> EngineConfig:
    return parse_engine_config(yaml.safe_load(CONFIG_FILE.read_text()))


@pytest.fixture
def build() -> Callable[..., EngineInput]:
    def _build(
        goal: str = "startup",
        scores: dict[str, float | None] | None = None,
        conf: dict[str, float] | None = None,
        n_sources: int = 5,
        n_platforms: int = 2,
        effort: float = 100,
        hpw: float = 10,
        weeks: float = 12,
        team: float = 2,
        risk: str | None = None,
        dup: str | None = None,
        objections: list[dict[str, str]] | None = None,
    ) -> EngineInput:
        score_map: dict[str, float | None] = dict(scores or GOOD)
        conf_map = conf or {d: 0.7 for d in DIMENSIONS}
        payload: dict[str, Any] = {
            "idea_id": "idea_t",
            "version": 1,
            "run_id": "run_t",
            "profile": {
                "goal": goal,
                "hours_per_week": hpw,
                "weeks_available": weeks,
                "team_size": team,
            },
            "scores": [
                {
                    "dimension": d,
                    "score": score_map[d],
                    "confidence": conf_map[d],
                    "evidence_ids": [],
                }
                for d in DIMENSIONS
            ],
            "gate_inputs": {
                "n_sources": n_sources,
                "n_platforms": n_platforms,
                "estimated_effort_hours": effort,
                "risk_blocker": risk,
                "duplicate_of": dup,
            },
            "objections_accepted": objections or [],
        }
        return EngineInput.model_validate(payload)

    return _build
