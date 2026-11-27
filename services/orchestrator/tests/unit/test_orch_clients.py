"""Typed clients over ServiceClient, driven by httpx.MockTransport fakes (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from common.errors import NotFound, UpstreamError, UpstreamTimeout, ValidationFailed
from common.http import ServiceClient
from svc_orchestrator.clients.analyst import AnalystClient
from svc_orchestrator.clients.base import retries_var
from svc_orchestrator.clients.critic import CriticClient
from svc_orchestrator.clients.kb import KbClient
from svc_orchestrator.clients.profile import ProfileClient
from svc_orchestrator.clients.scouts import ScoutsClient
from svc_orchestrator.clients.validators import ValidatorsClient
from svc_orchestrator.clients.verdict import VerdictClient

Handler = Callable[[httpx.Request], httpx.Response]
EVIDENCE = {
    "evidence_id": "card_8f21a0b1#c2",
    "card_id": "card_8f21a0b1",
    "quote": "export is slow",
    "source": "hackernews",
    "url": "https://news.ycombinator.com/item?id=1",
    "retrieved_at": "2026-05-01T00:00:00Z",
    "score": 0.9,
    "rank": 1,
    "card_name": "Notes",
}


def http(handler: Handler, *, retries: int = 2) -> ServiceClient:
    return ServiceClient(
        "http://svc",
        timeout_s=1,
        max_retries=retries,
        internal_token="tok",
        backoff_s=0.0,
        transport=httpx.MockTransport(handler),
    )


def reply(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


class Recorder:
    def __init__(self, response: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response(request)

    @property
    def last_json(self) -> Any:
        return json.loads(self.requests[-1].content)


# ---- M6 ------------------------------------------------------------------------------------
async def test_kb_search_posts_the_documented_body_and_parses_items() -> None:
    rec = Recorder(lambda r: reply({"items": [EVIDENCE], "index_version": 12, "cache_hit": False}))
    result = await KbClient(http(rec)).search("slow export", run_id="run_1", k=7)
    assert rec.requests[0].url.path == "/search"
    assert rec.last_json == {
        "query": "slow export",
        "k": 7,
        "mode": "hybrid",
        "rerank": True,
        "run_id": "run_1",
    }
    assert rec.requests[0].headers["X-Internal-Token"] == "tok"
    assert [e.evidence_id for e in result.items] == ["card_8f21a0b1#c2"]
    assert result.index_version == 12
    assert result.skipped == 0


async def test_kb_search_skips_malformed_items_instead_of_trusting_them() -> None:
    bad = {**EVIDENCE, "evidence_id": "not-an-id"}
    rec = Recorder(lambda r: reply({"items": [bad, EVIDENCE], "index_version": 1}))
    result = await KbClient(http(rec)).search("q", run_id="r")
    assert len(result.items) == 1
    assert result.skipped == 1


async def test_kb_search_with_no_items_key_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await KbClient(http(lambda r: reply({"nope": 1}))).search("q", run_id="r")


async def test_invalid_json_body_is_an_upstream_error_not_a_crash() -> None:
    def garbage(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    with pytest.raises(UpstreamError):
        await KbClient(http(garbage)).search("q", run_id="r")


# ---- retries and timeouts ------------------------------------------------------------------
async def test_a_503_is_retried_and_the_retry_count_is_exposed() -> None:
    calls = {"n": 0}

    def flaky(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return reply({"items": []}, 503) if calls["n"] < 3 else reply({"items": []})

    retries_var.set(0)
    await KbClient(http(flaky)).search("q", run_id="r")
    assert calls["n"] == 3
    assert retries_var.get() == 2


async def test_a_timeout_is_retried_twice_then_raises_upstream_timeout() -> None:
    calls = {"n": 0}

    def hang(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamTimeout):
        await KbClient(http(hang)).search("q", run_id="r")
    assert calls["n"] == 3  # 1 try + 2 retries, never more


async def test_a_422_is_never_retried() -> None:
    rec = Recorder(lambda r: reply({"error": {}}, 422))
    with pytest.raises(ValidationFailed):
        await KbClient(http(rec)).search("q", run_id="r")
    assert len(rec.requests) == 1


# ---- M7 / M4 -------------------------------------------------------------------------------
async def test_analyst_features_returns_the_feature_dict() -> None:
    rec = Recorder(lambda r: reply({"n_sources": 4, "trend_growth": 0.2}))
    out = await AnalystClient(http(rec)).features("idea", ["card_8f21a0b1#c2"], {"goal": "startup"})
    assert out == {"n_sources": 4, "trend_growth": 0.2}
    assert rec.requests[0].url.path == "/features"
    assert rec.last_json == {
        "idea_text": "idea",
        "evidence_ids": ["card_8f21a0b1#c2"],
        "profile": {"goal": "startup"},
    }


async def test_analyst_features_must_be_an_object() -> None:
    with pytest.raises(UpstreamError):
        await AnalystClient(http(lambda r: reply([1, 2]))).features("i", [], {})


async def test_scouts_posts_source_and_max_pages() -> None:
    rec = Recorder(lambda r: reply({"fetched": 3, "raw_page_ids": ["a", "b", "c"]}))
    out = await ScoutsClient(http(rec)).scout("hackernews", max_pages=5, query="notes")
    assert out == {"fetched": 3, "raw_page_ids": ["a", "b", "c"]}
    assert rec.last_json == {"source": "hackernews", "query": "notes", "max_pages": 5}


# ---- M9 ------------------------------------------------------------------------------------
async def test_score_all_returns_raw_scores_and_extra() -> None:
    rec = Recorder(lambda r: reply({"scores": [{"dimension": "risk"}], "extra": {"x": 1}}))
    out = await ValidatorsClient(http(rec)).score_all(
        run_id="run_1",
        idea="idea",
        profile={"goal": "startup"},
        evidence=[{"evidence_id": "e", "quote": "q"}],
        features={"f": 1},
    )
    assert out.scores == [{"dimension": "risk"}]
    assert out.extra == {"x": 1}
    assert rec.requests[0].url.path == "/score-all"
    assert set(rec.last_json) == {"run_id", "idea", "profile", "evidence", "features"}


async def test_score_all_tolerates_a_missing_extra_block() -> None:
    out = await ValidatorsClient(http(lambda r: reply({"scores": []}))).score_all(
        run_id="r", idea="i", profile={}, evidence=[], features={}
    )
    assert out.extra == {}


async def test_score_all_without_a_scores_list_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await ValidatorsClient(http(lambda r: reply({"scores": "oops"}))).score_all(
            run_id="r", idea="i", profile={}, evidence=[], features={}
        )


# ---- M11 -----------------------------------------------------------------------------------
async def test_verdict_config_exposes_versions_and_rubric_versions() -> None:
    body = {
        "rules": {"rubric_versions": {"risk": "risk@v1"}},
        "versions": {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"},
    }
    cfg = await VerdictClient(http(lambda r: reply(body))).config()
    assert cfg.engine == "m11@v1"
    assert cfg.rubrics == "r1"
    assert cfg.weights == "w1"
    assert cfg.rubric_versions == {"risk": "risk@v1"}


async def test_voi_parses_the_response() -> None:
    body = {
        "low": {"value": 80, "verdict": "GO", "stability": "STABLE", "score": 75.0},
        "high": {"value": 300, "verdict": "NO-GO", "stability": "STABLE", "score": 40.0},
        "verdict_changes": True,
    }
    rec = Recorder(lambda r: reply(body))
    out = await VerdictClient(http(rec)).voi({"input": {}, "unknown": {}, "range": [80, 300]})
    assert out.verdict_changes is True
    assert (out.low.verdict, out.high.verdict) == ("GO", "NO-GO")
    assert rec.requests[0].url.path == "/value-of-information"


async def test_verdict_response_must_be_a_valid_verdict() -> None:
    with pytest.raises(UpstreamError):
        await VerdictClient(http(lambda r: reply({"verdict": "GO"}))).verdict({})


# ---- M12 -----------------------------------------------------------------------------------
async def test_critic_check_parses_results() -> None:
    body = {
        "results": [{"claim_id": "cl_risk", "supported": False, "reason": "quote does not support"}]
    }
    rec = Recorder(lambda r: reply(body))
    out = await CriticClient(http(rec)).check(
        "run_1", [{"claim_id": "cl_risk", "text": "t", "evidence_ids": ["card_8f21a0b1#c2"]}]
    )
    assert out.results[0].supported is False
    assert rec.requests[0].url.path == "/check"
    assert rec.last_json["run_id"] == "run_1"


async def test_critic_check_with_garbage_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await CriticClient(http(lambda r: reply({"results": [{"claim_id": 1}]}))).check("r", [])


async def test_approval_evaluate_parses_the_decision() -> None:
    rec = Recorder(lambda r: reply({"needs_approval": True, "spent": 0.04, "budget": 0.05}))
    out = await CriticClient(http(rec)).evaluate_approval("run_1", "deep_search", 0.016)
    assert out.needs_approval is True
    assert (out.spent, out.budget) == (0.04, 0.05)
    assert rec.requests[0].url.path == "/approval/evaluate"
    assert rec.last_json == {
        "run_id": "run_1",
        "action": "deep_search",
        "estimated_cost_usd": 0.016,
    }


# ---- M2 ------------------------------------------------------------------------------------
PROFILE = {
    "user_id": "u1",
    "skills": ["python"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "startup",
    "interests": [],
    "version": 3,
}


async def test_profile_get_parses_a_student_profile() -> None:
    rec = Recorder(lambda r: reply(PROFILE))
    profile = await ProfileClient(http(rec), http(rec)).get_profile("u1")
    assert profile.version == 3
    assert rec.requests[0].url.path == "/internal/profiles/u1"


async def test_profile_not_found_is_a_not_found_error() -> None:
    with pytest.raises(NotFound):
        await ProfileClient(http(lambda r: reply({}, 404)), http(lambda r: reply({}))).get_profile(
            "u"
        )


async def test_cache_get_returns_value_on_hit_and_none_on_miss() -> None:
    hit = Recorder(lambda r: reply({"hit": True, "value": {"a": 1}}))
    miss = Recorder(lambda r: reply({"hit": False, "value": None}))
    key = "verdict:i:1:abc:p1:rr1:ww1:em11@v1"
    assert await ProfileClient(http(lambda r: reply({})), http(hit)).get_cached_verdict(key) == {
        "a": 1
    }
    assert (
        await ProfileClient(http(lambda r: reply({})), http(miss)).get_cached_verdict(key) is None
    )
    assert hit.requests[0].url.path == f"/internal/cache/verdict/{key}"


async def test_cache_put_sends_value_and_ttl() -> None:
    rec = Recorder(lambda r: reply({}))
    await ProfileClient(http(lambda r: reply({})), http(rec)).put_cached_verdict(
        "k", {"a": 1}, 86400
    )
    assert rec.requests[0].method == "PUT"
    assert rec.last_json == {"value": {"a": 1}, "ttl_s": 86400}


async def test_put_run_sends_the_record() -> None:
    rec = Recorder(lambda r: reply({}))
    await ProfileClient(http(rec), http(lambda r: reply({}))).put_run("run_1", {"state": "running"})
    assert rec.requests[0].method == "PUT"
    assert rec.requests[0].url.path == "/internal/runs/run_1"
    assert rec.last_json == {"state": "running"}
