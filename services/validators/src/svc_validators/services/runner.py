"""M9 orchestration: four scorers, six dimensions (ADR-004), prior + M10 + blend (ADR-006).

Every scorer is independent: they run under ``asyncio.gather(..., return_exceptions=True)`` with a
per-scorer timeout, so one slow or failing scorer becomes an abstention with an error note and
never cancels the others. Models never produce a verdict; this service only produces scores.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from common.errors import DomainError, NotFound, UpstreamError
from common.logging import get_logger
from common.schemas import DIMENSIONS, DimensionScore, ScoreRequest
from common.schemas.types import Dimension

from ..models.api import ExtraOutputs, ScoreAllRequest, ScoreAllResponse, ScorerError
from ..models.rubric import Rubric
from .blend import blend, round_half, scaled_confidence
from .prior import PriorCalculator

log = get_logger("validators")

# ADR-004: Feasibility-for-this-student also produces the Learning score.
SCORERS: dict[str, tuple[str, ...]] = {
    "demand_pain": ("demand_pain",),
    "competition": ("competition", "differentiation"),
    "feasibility": ("feasibility", "learning"),
    "risk": ("risk",),
}
PROFILE_DIMENSIONS = frozenset({"feasibility", "learning"})
REASON_MAX = 600
_CARD_ID = re.compile(r"^card_[0-9a-f]{8,12}$")


class ScorerPort(Protocol):
    async def score(self, request: ScoreRequest) -> DimensionScore: ...


@dataclass
class _Outcome:
    scores: list[DimensionScore] = field(default_factory=list)
    errors: list[ScorerError] = field(default_factory=list)


def _error_note(exc: BaseException) -> str:
    """A short, safe description: never the exception message of an unknown error."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, DomainError):
        return f"{exc.code.value}: {exc.message}"[:200]
    return type(exc).__name__


def _clip(text: str, limit: int = REASON_MAX) -> str:
    return text[:limit]


def extra_outputs(features: Mapping[str, Any]) -> ExtraOutputs:
    hours = features.get("estimated_effort_hours")
    skills = features.get("required_skills")
    blocker = features.get("risk_blocker")
    duplicate = features.get("duplicate_of")
    return ExtraOutputs(
        estimated_effort_hours=(
            float(hours)
            if isinstance(hours, int | float) and not isinstance(hours, bool) and hours >= 0
            else None
        ),
        required_skills=(
            list(skills)
            if isinstance(skills, list) and all(isinstance(s, str) for s in skills)
            else None
        ),
        risk_blocker=blocker if isinstance(blocker, str) and blocker.strip() else None,
        duplicate_of=duplicate
        if isinstance(duplicate, str) and _CARD_ID.match(duplicate)
        else None,
    )


class ValidationRunner:
    def __init__(
        self,
        scorer: ScorerPort,
        rubrics: Mapping[str, Rubric],
        *,
        alpha: float,
        scorer_timeout_s: float,
    ) -> None:
        if not 0 <= alpha <= 1:
            raise ValueError("alpha must be within [0, 1]")
        self._scorer = scorer
        self._rubrics = dict(rubrics)
        self._priors = {d: PriorCalculator(r) for d, r in self._rubrics.items()}
        self._alpha = alpha
        self._timeout = scorer_timeout_s

    # ---- per dimension -------------------------------------------------------------------

    def _abstain(
        self,
        dimension: str,
        features: dict[str, Any],
        reason: str,
        *,
        model: str = "unavailable",
        fallback: bool | None = None,
    ) -> DimensionScore:
        return DimensionScore(
            dimension=cast(Dimension, dimension),
            score=None,
            confidence=0.0,
            evidence_ids=[],
            features=features,
            reason=_clip(reason),
            rubric_version=self._rubrics[dimension].id,
            model=model,
            abstained=True,
            fallback=fallback,
        )

    async def _score_dimension(self, dimension: str, request: ScoreAllRequest) -> DimensionScore:
        rubric = self._rubrics[dimension]
        features = {k: request.features[k] for k in rubric.features if k in request.features}
        provided = list(dict.fromkeys(e.evidence_id for e in request.evidence))
        if not provided:
            return self._abstain(dimension, features, "no evidence")  # evidence or abstain

        prior = self._priors[dimension].compute(request.features)
        result = await self._scorer.score(
            ScoreRequest(
                rubric_id=rubric.id,
                idea=request.idea,
                profile=request.profile if dimension in PROFILE_DIMENSIONS else None,
                evidence=request.evidence,
                features=features,
                run_id=request.run_id,
            )
        )
        if result.dimension != dimension or result.rubric_version != rubric.id:
            raise UpstreamError(f"M10 answered {result.dimension}/{result.rubric_version}")

        valid = [i for i in result.evidence_ids if i in provided]
        if result.score is None or not valid:
            return self._abstain(
                dimension,
                features,
                result.reason if result.score is None and result.reason else "no valid evidence",
                model=result.model,
                fallback=result.fallback,
            )

        if prior.score is None:
            final = round_half(result.score)
            note = f" [model {result.score}; prior unavailable: {prior.problem}]"
        else:
            final = blend(result.score, prior.score, self._alpha)
            note = f" [model {result.score}, prior {prior.score}, alpha {self._alpha}]"
        reason = _clip(result.reason, REASON_MAX - len(note)) + note
        return DimensionScore(
            dimension=result.dimension,
            score=final,
            confidence=scaled_confidence(result.confidence, len(valid)),
            evidence_ids=valid,
            features={
                **features,
                "feature_prior": prior.score,
                "model_score": result.score,
                "alpha_model": self._alpha,
            },
            reason=reason,
            rubric_version=rubric.id,
            model=result.model,
            abstained=False,
            fallback=result.fallback,
        )

    # ---- per scorer ----------------------------------------------------------------------

    async def _run_scorer(self, name: str, request: ScoreAllRequest) -> _Outcome:
        dimensions = SCORERS[name]
        results = await asyncio.gather(
            *(self._score_dimension(d, request) for d in dimensions), return_exceptions=True
        )
        outcome = _Outcome()
        for dimension, result in zip(dimensions, results, strict=True):
            if isinstance(result, DimensionScore):
                outcome.scores.append(result)
                continue
            if isinstance(result, asyncio.CancelledError):
                raise result
            self._record_failure(outcome, name, request, result, dimension)
        return outcome

    def _record_failure(
        self,
        outcome: _Outcome,
        name: str,
        request: ScoreAllRequest,
        exc: BaseException,
        dimension: str | None,
    ) -> None:
        note = _error_note(exc)
        log.warning(
            "scorer_failed", run_id=request.run_id, scorer=name, dimension=dimension, error=note
        )
        dims = (dimension,) if dimension else SCORERS[name]
        for d in dims:
            features = {
                k: request.features[k] for k in self._rubrics[d].features if k in request.features
            }
            outcome.scores.append(self._abstain(d, features, f"scorer error: {note}"))
        outcome.errors.append(
            ScorerError(scorer=name, dimension=cast("Dimension | None", dimension), error=note)
        )

    async def _guarded(self, name: str, request: ScoreAllRequest) -> _Outcome:
        return await asyncio.wait_for(self._run_scorer(name, request), timeout=self._timeout)

    async def _run(self, names: list[str], request: ScoreAllRequest) -> ScoreAllResponse:
        results = await asyncio.gather(
            *(self._guarded(n, request) for n in names), return_exceptions=True
        )
        scores: list[DimensionScore] = []
        errors: list[ScorerError] = []
        for name, result in zip(names, results, strict=True):
            if isinstance(result, _Outcome):
                scores.extend(result.scores)
                errors.extend(result.errors)
                continue
            if isinstance(result, asyncio.CancelledError):
                raise result
            failed = _Outcome()
            self._record_failure(failed, name, request, result, None)
            scores.extend(failed.scores)
            errors.extend(failed.errors)
        scores.sort(key=lambda s: DIMENSIONS.index(s.dimension))
        return ScoreAllResponse(
            scores=scores,
            abstained=[s.dimension for s in scores if s.score is None],
            extra=extra_outputs(request.features),
            errors=errors,
        )

    # ---- public --------------------------------------------------------------------------

    async def score_all(self, request: ScoreAllRequest) -> ScoreAllResponse:
        return await self._run(list(SCORERS), request)

    async def score_one(self, scorer: str, request: ScoreAllRequest) -> ScoreAllResponse:
        if scorer not in SCORERS:
            raise NotFound(f"unknown scorer {scorer!r}")
        return await self._run([scorer], request)
