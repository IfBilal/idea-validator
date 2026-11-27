"""The M9 runner: prior + M10 call + blend, abstention paths, parallelism and failure isolation."""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from common.errors import UpstreamError, UpstreamTimeout
from common.schemas import DimensionScore, ScoreEvidence, ScoreRequest, StudentProfile
from svc_validators.models.api import ScoreAllRequest
from svc_validators.services.rubrics import load_rubrics
from svc_validators.services.runner import SCORERS, ValidationRunner

ROOT = Path(__file__).resolve().parents[4]
DIMENSIONS = ["demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"]
ID1, ID2 = "card_8f21a3b4#c2", "card_77a1b2c3#c0"
RUBRICS = load_rubrics(ROOT / "rubrics", dict.fromkeys(DIMENSIONS, 1))

FEATURES: dict[str, Any] = {
    "n_sources": 4,
    "n_platforms": 2,
    "complaint_count": 3,
    "trend_growth": 0.2,
    "recency_share": 0.6,
    "n_competitors": 1,
    "niche_saturation": 0.5,
    "competitor_strength": 0.2,
    "unaddressed_gap_count": 1,
    "max_similarity": 0.7,
    "estimated_effort_hours": 50,
    "available_hours": 100,
    "skill_overlap": 0.9,
    "compute_fit": True,
    "skill_novelty": 2,
    "cv_signal": 0.6,
    "risk_flags": [],
    "platform_dependency": False,
}
# priors for FEATURES: demand 4, competition 4, differentiation 4, feasibility 5, learning 4, risk 5


def model_score(
    request: ScoreRequest,
    score: float | None = 3.0,
    confidence: float = 0.8,
    ids: list[str] | None = None,
    **over: Any,
) -> DimensionScore:
    dimension: Any = request.rubric_id.split("@")[0]
    fields: dict[str, Any] = {
        "dimension": dimension,
        "score": score,
        "confidence": confidence if score is not None else 0,
        "evidence_ids": [ID1] if ids is None else ids,
        "reason": "model reason",
        "rubric_version": request.rubric_id,
        "model": "hosted-default",
        "abstained": score is None,
        "fallback": False,
    }
    fields.update(over)
    return DimensionScore(**fields)


class FakeScorer:
    def __init__(self, handler: Callable[[ScoreRequest], Any] | None = None) -> None:
        self.handler = handler or (lambda r: model_score(r))
        self.requests: list[ScoreRequest] = []

    async def score(self, request: ScoreRequest) -> DimensionScore:
        self.requests.append(request)
        result = self.handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, DimensionScore)
        return result


def make_request(**over: Any) -> ScoreAllRequest:
    fields: dict[str, Any] = {
        "run_id": "run_1",
        "idea": "A study planner",
        "evidence": [
            ScoreEvidence(evidence_id=ID1, quote="export is slow"),
            ScoreEvidence(evidence_id=ID2, quote="no offline mode"),
        ],
        "features": FEATURES,
    }
    fields.update(over)
    return ScoreAllRequest(**fields)


def runner(scorer: FakeScorer, alpha: float = 0.6, timeout_s: float = 5.0) -> ValidationRunner:
    return ValidationRunner(scorer, RUBRICS, alpha=alpha, scorer_timeout_s=timeout_s)


def by_dim(scores: list[DimensionScore]) -> dict[str, DimensionScore]:
    return {s.dimension: s for s in scores}


async def test_score_all_returns_six_dimensions_in_canonical_order() -> None:
    response = await runner(FakeScorer()).score_all(make_request())
    assert [s.dimension for s in response.scores] == DIMENSIONS
    assert response.abstained == []
    assert response.errors == []


async def test_scores_are_blended_with_the_prior() -> None:
    response = await runner(FakeScorer()).score_all(make_request())
    scores = by_dim(response.scores)
    # alpha 0.6, model 3: 0.6*3 + 0.4*prior, rounded to 0.5
    assert scores["demand_pain"].score == 3.5  # prior 4 -> 3.4
    assert scores["feasibility"].score == 4.0  # prior 5 -> 3.8
    assert scores["risk"].score == 4.0  # prior 5 -> 3.8
    assert scores["learning"].score == 3.5  # prior 4 -> 3.4
    assert scores["feasibility"].features is not None
    assert scores["feasibility"].features["feature_prior"] == 5
    assert scores["feasibility"].features["model_score"] == 3.0
    assert scores["feasibility"].features["alpha_model"] == 0.6
    assert scores["demand_pain"].rubric_version == "demand_pain@v1"
    assert scores["demand_pain"].model == "hosted-default"
    assert scores["demand_pain"].abstained is False


@pytest.mark.parametrize("alpha,expected", [(0.0, 4.0), (1.0, 3.0)])
async def test_alpha_extremes(alpha: float, expected: float) -> None:
    response = await runner(FakeScorer(), alpha=alpha).score_all(make_request())
    assert by_dim(response.scores)["demand_pain"].score == expected


async def test_confidence_scales_with_valid_evidence_count() -> None:
    one = await runner(FakeScorer()).score_all(make_request())
    assert by_dim(one.scores)["demand_pain"].confidence == pytest.approx(0.8 / 3)

    three_ids = [ID1, ID2, "card_aaaaaaaa#c1"]
    request = make_request(
        evidence=[ScoreEvidence(evidence_id=i, quote="q") for i in three_ids],
    )
    scorer = FakeScorer(lambda r: model_score(r, ids=three_ids))
    full = await runner(scorer).score_all(request)
    assert by_dim(full.scores)["demand_pain"].confidence == pytest.approx(0.8)


async def test_no_evidence_abstains_everywhere_without_calling_m10() -> None:
    scorer = FakeScorer()
    response = await runner(scorer).score_all(make_request(evidence=[]))
    assert scorer.requests == []
    assert response.abstained == DIMENSIONS
    for s in response.scores:
        assert (s.score, s.confidence, s.evidence_ids) == (None, 0, [])
        assert s.reason == "no evidence"
        assert s.abstained is True


async def test_model_abstention_is_not_rescued_by_the_prior() -> None:
    scorer = FakeScorer(lambda r: model_score(r, score=None, ids=[]))
    response = await runner(scorer).score_all(make_request())
    assert response.abstained == DIMENSIONS
    assert all(s.score is None and s.confidence == 0 for s in response.scores)


async def test_fallback_flag_from_m10_is_preserved_on_abstention() -> None:
    scorer = FakeScorer(lambda r: model_score(r, score=None, ids=[], fallback=True))
    response = await runner(scorer).score_all(make_request())
    assert all(s.fallback is True for s in response.scores)


async def test_unknown_evidence_ids_from_m10_are_dropped() -> None:
    scorer = FakeScorer(lambda r: model_score(r, ids=[ID1, "card_deadbeef#c9"]))
    response = await runner(scorer).score_all(make_request())
    assert by_dim(response.scores)["risk"].evidence_ids == [ID1]


async def test_only_unknown_evidence_ids_abstain() -> None:
    scorer = FakeScorer(lambda r: model_score(r, ids=["card_deadbeef#c9"]))
    response = await runner(scorer).score_all(make_request())
    assert response.abstained == DIMENSIONS


async def test_missing_features_fall_back_to_model_only_and_say_so() -> None:
    response = await runner(FakeScorer()).score_all(make_request(features={"n_sources": 4}))
    demand = by_dim(response.scores)["demand_pain"]
    assert demand.score == 3.0  # model only: prior unavailable
    assert demand.features is not None and demand.features["feature_prior"] is None
    assert "prior unavailable" in demand.reason


async def test_request_to_m10_carries_rubric_evidence_and_only_rubric_features() -> None:
    scorer = FakeScorer()
    await runner(scorer).score_all(make_request(features={**FEATURES, "secret_extra": "x"}))
    sent = {r.rubric_id: r for r in scorer.requests}
    assert set(sent) == {f"{d}@v1" for d in DIMENSIONS}
    risk = sent["risk@v1"]
    assert set(risk.features) == {"risk_flags", "platform_dependency"}
    assert [e.evidence_id for e in risk.evidence] == [ID1, ID2]
    assert risk.run_id == "run_1"
    assert all("secret_extra" not in r.features for r in scorer.requests)


async def test_profile_is_sent_only_for_feasibility_and_learning() -> None:
    profile = StudentProfile(
        user_id="u1",
        skills=["python"],
        hours_per_week=6,
        weeks_available=8,
        team_size=2,
        compute="laptop",
        goal="course_project",
        interests=[],
    )
    scorer = FakeScorer()
    await runner(scorer).score_all(make_request(profile=profile))
    with_profile = {r.rubric_id for r in scorer.requests if r.profile is not None}
    assert with_profile == {"feasibility@v1", "learning@v1"}


async def test_one_scorer_failing_does_not_cancel_the_others() -> None:
    def handler(request: ScoreRequest) -> Any:
        if request.rubric_id.startswith("risk"):
            return UpstreamError("M10 down")
        return model_score(request)

    response = await runner(FakeScorer(handler)).score_all(make_request())
    scores = by_dim(response.scores)
    assert scores["risk"].score is None and scores["risk"].abstained is True
    assert "scorer error" in scores["risk"].reason
    assert scores["demand_pain"].score is not None
    assert scores["competition"].score is not None
    assert response.abstained == ["risk"]
    assert [e.scorer for e in response.errors] == ["risk"]
    assert response.errors[0].error.startswith("UPSTREAM_ERROR")


async def test_failure_of_one_dimension_keeps_its_sibling() -> None:
    def handler(request: ScoreRequest) -> Any:
        if request.rubric_id.startswith("learning"):
            return UpstreamTimeout("slow")
        return model_score(request)

    response = await runner(FakeScorer(handler)).score_all(make_request())
    scores = by_dim(response.scores)
    assert scores["feasibility"].score is not None
    assert scores["learning"].score is None
    assert response.errors[0].scorer == "feasibility"
    assert response.errors[0].dimension == "learning"


async def test_slow_scorer_times_out_without_blocking_the_others() -> None:
    async def slow(request: ScoreRequest) -> DimensionScore:
        if request.rubric_id.startswith(("competition", "differentiation")):
            await asyncio.sleep(5)
        return model_score(request)

    started = time.perf_counter()
    response = await runner(FakeScorer(slow), timeout_s=0.1).score_all(make_request())
    assert time.perf_counter() - started < 1.5
    scores = by_dim(response.scores)
    assert scores["competition"].score is None and scores["differentiation"].score is None
    assert "timeout" in scores["competition"].reason
    assert scores["demand_pain"].score is not None
    assert [e.scorer for e in response.errors] == ["competition"]
    assert response.errors[0].error == "timeout"


async def test_scorers_run_in_parallel() -> None:
    async def slowish(request: ScoreRequest) -> DimensionScore:
        await asyncio.sleep(0.2)
        return model_score(request)

    started = time.perf_counter()
    await runner(FakeScorer(slowish)).score_all(make_request())
    assert time.perf_counter() - started < 0.6  # 6 calls of 0.2s would take 1.2s serially


async def test_m10_answering_for_the_wrong_dimension_is_rejected_as_a_scorer_error() -> None:
    def handler(request: ScoreRequest) -> DimensionScore:
        return model_score(request, dimension="learning")

    response = await runner(FakeScorer(handler)).score_all(make_request())
    assert by_dim(response.scores)["demand_pain"].score is None
    assert response.errors


async def test_m10_answering_with_another_rubric_version_is_rejected() -> None:
    def handler(request: ScoreRequest) -> DimensionScore:
        return model_score(request, rubric_version="demand_pain@v9")

    response = await runner(FakeScorer(handler)).score_one("demand_pain", make_request())
    assert response.scores[0].score is None
    assert response.errors


async def test_single_scorer_runs_only_its_dimensions() -> None:
    scorer = FakeScorer()
    response = await runner(scorer).score_one("feasibility", make_request())
    assert [s.dimension for s in response.scores] == ["feasibility", "learning"]
    assert {r.rubric_id for r in scorer.requests} == {"feasibility@v1", "learning@v1"}


async def test_unknown_scorer_name_is_not_found() -> None:
    from common.errors import NotFound

    with pytest.raises(NotFound):
        await runner(FakeScorer()).score_one("verdict", make_request())


def test_scorer_registry_matches_adr_004() -> None:
    assert SCORERS == {
        "demand_pain": ("demand_pain",),
        "competition": ("competition", "differentiation"),
        "feasibility": ("feasibility", "learning"),
        "risk": ("risk",),
    }


async def test_extra_outputs_come_from_features() -> None:
    features = {
        **FEATURES,
        "estimated_effort_hours": 120,
        "required_skills": ["python", "sql"],
        "risk_blocker": "scraping a logged-in site",
        "duplicate_of": "card_8f21a3b4",
    }
    extra = (await runner(FakeScorer()).score_all(make_request(features=features))).extra
    assert extra.estimated_effort_hours == 120
    assert extra.required_skills == ["python", "sql"]
    assert extra.risk_blocker == "scraping a logged-in site"
    assert extra.duplicate_of == "card_8f21a3b4"


@pytest.mark.parametrize("bad", ["card_xyz", "Notion", 5, "", None, ["card_8f21a3b4"]])
async def test_duplicate_of_must_be_a_kb_card_id(bad: Any) -> None:
    features = {**FEATURES, "duplicate_of": bad}
    extra = (await runner(FakeScorer()).score_all(make_request(features=features))).extra
    assert extra.duplicate_of is None


@pytest.mark.parametrize("bad", [-1, "many", True, None])
async def test_bad_effort_hours_become_none(bad: Any) -> None:
    features = {**FEATURES, "estimated_effort_hours": bad}
    extra = (await runner(FakeScorer()).score_all(make_request(features=features))).extra
    assert extra.estimated_effort_hours is None


async def test_failure_note_does_not_leak_exception_internals() -> None:
    def handler(request: ScoreRequest) -> Any:
        if request.rubric_id.startswith("risk"):
            return RuntimeError("secret connection string postgres://u:p@h")
        return model_score(request)

    response = await runner(FakeScorer(handler)).score_all(make_request())
    assert "postgres" not in response.errors[0].error
    assert "postgres" not in by_dim(response.scores)["risk"].reason
