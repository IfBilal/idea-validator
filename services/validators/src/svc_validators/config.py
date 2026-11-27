import math
from pathlib import Path

from pydantic import Field

from common.config import ConfigError, ServiceSettings, load_yaml_config

DEFAULT_ALPHA = 0.6  # ADR-006
DEFAULT_RUBRIC_VERSIONS = {
    "demand_pain": 1,
    "competition": 1,
    "differentiation": 1,
    "feasibility": 1,
    "risk": 1,
    "learning": 1,
}


class Settings(ServiceSettings):
    """M9 settings (env / ConfigMap)."""

    service_name: str = "validators"
    scorer_url: str | None = None  # M10 base URL
    scorer_timeout_s: float = 60.0  # config/budgets.yaml timeouts_s.scorer; per-scorer cap
    scorer_max_retries: int = 2
    rubrics_dir: Path = Path("/app/rubrics")
    # exact rubric versions, one per dimension
    rubric_versions: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_RUBRIC_VERSIONS))
    alpha_model: float | None = None  # overrides config/verdict.yaml scoring_blend.alpha_model


def _checked(value: object, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{source}: alpha_model must be a number")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ConfigError(f"{source}: alpha_model must be within [0, 1]")
    return float(value)


def resolve_alpha(settings: Settings) -> float:
    """env ``ALPHA_MODEL`` > ``verdict.yaml`` ``scoring_blend.alpha_model`` > 0.6."""
    if settings.alpha_model is not None:
        return _checked(settings.alpha_model, "ALPHA_MODEL")
    try:
        data = load_yaml_config("verdict", config_dir=settings.config_dir)
    except ConfigError as exc:
        if "not found" in str(exc):
            return DEFAULT_ALPHA
        raise
    blend = data.get("scoring_blend")
    if blend is None:
        return DEFAULT_ALPHA
    if not isinstance(blend, dict):
        raise ConfigError("verdict.yaml: scoring_blend must be a mapping")
    if "alpha_model" not in blend:
        return DEFAULT_ALPHA
    return _checked(blend["alpha_model"], "verdict.yaml")
