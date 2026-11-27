"""Typed clients for M8, M7 (discover contract), M12 objection filter and M2 idea versions."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from common.errors import NotFound, UpstreamError, ValidationFailed
from common.http import ServiceClient
from svc_orchestrator.clients.analyst import AnalystClient
from svc_orchestrator.clients.critic import CriticClient
from svc_orchestrator.clients.ideation import IdeationClient
from svc_orchestrator.clients.profile import ProfileClient

E1 = "card_8f21a0b1#c0"
IDEA: dict[str, Any] = {
    "title": "Dockerfile linter",
    "summary": "Lints Dockerfiles",
    "target_user": "students",
    "core_features": ["a", "b", "c"],
    "gap_ref": "gap_1",
    "gap_evidence_ids": [E1],
    "fit_reason": "fits",
    "est_scope": "M",
    "idea_ref": "idea_abc",
    "niche": "devtools",
    "complaints": [],
    "missing_features": [],
}
OBJ = {
    "objection_id": "obj_1",
    "idea_ref": "idea_abc",
    "dimension": "competition",
    "claim": "Hadolint exists",
    "severity": "major",
    "evidence_ids": [E1],
}


def http(handler: Callable[[httpx.Request], httpx.Response]) -> ServiceClient:
    return ServiceClient(
        "http://svc",
        timeout_s=1,
        max_retries=2,
        backoff_s=0.0,
        transport=httpx.MockTransport(handler),
    )


class Rec:
    def __init__(self, body: Any, status: int = 200) -> None:
        self.body, self.status = body, status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def sent(self) -> Any:
        return json.loads(self.requests[-1].content)


PROFILE = {
    "user_id": "u1",
    "skills": [],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "startup",
    "interests": [],
    "version": 3,
}


# ---- M8 ------------------------------------------------------------------------------------
async def test_ideate_posts_the_documented_body_and_parses_ideas() -> None:
    rec = Rec({"ideas": [IDEA], "dropped": [], "reason": None, "round": 1, "prompt_version": "v1"})
    out = await IdeationClient(http(rec)).ideate(
        run_id="run_1",
        profile=PROFILE,
        niches=[{"name": "devtools"}],
        gaps=[],
        round_=2,
        previous_objections=[OBJ],
        count=8,
    )
    assert rec.requests[0].url.path == "/ideate"
    assert rec.sent["round"] == 2
    assert rec.sent["count"] == 8
    assert rec.sent["previous_objections"] == [OBJ]
    assert [i.idea_ref for i in out.ideas] == ["idea_abc"]
    assert out.reason is None


async def test_ideate_with_no_ideas_keeps_the_reason() -> None:
    rec = Rec({"ideas": [], "dropped": [], "reason": "model returned invalid JSON", "round": 1})
    out = await IdeationClient(http(rec)).ideate(
        run_id="r", profile=PROFILE, niches=[], gaps=[], round_=1, previous_objections=[], count=5
    )
    assert out.ideas == []
    assert out.reason == "model returned invalid JSON"


async def test_a_malformed_idea_is_skipped_not_trusted() -> None:
    bad = {**IDEA, "est_scope": "XL"}
    rec = Rec({"ideas": [bad, IDEA], "round": 1})
    out = await IdeationClient(http(rec)).ideate(
        run_id="r", profile=PROFILE, niches=[], gaps=[], round_=1, previous_objections=[], count=5
    )
    assert [i.idea_ref for i in out.ideas] == ["idea_abc"]
    assert out.skipped == 1


async def test_ideate_without_an_ideas_list_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await IdeationClient(http(Rec({"nope": 1}))).ideate(
            run_id="r",
            profile=PROFILE,
            niches=[],
            gaps=[],
            round_=1,
            previous_objections=[],
            count=5,
        )


async def test_gap_analyst_returns_enriched_ideas() -> None:
    enriched = {**IDEA, "complaints": [{"text": "slow", "evidence_ids": [E1]}]}
    rec = Rec({"ideas": [enriched], "discarded": 0, "prompt_version": "v1"})
    out = await IdeationClient(http(rec)).gap_analyst("run_1", [IDEA])
    assert rec.requests[0].url.path == "/gap-analyst"
    assert out[0].complaints[0].text == "slow"


async def test_skeptic_returns_objections_and_sends_scores_when_given() -> None:
    rec = Rec({"objections": [OBJ], "discarded": 0, "prompt_version": "v1"})
    out = await IdeationClient(http(rec)).skeptic(
        "run_1", [IDEA], profile=PROFILE, scores={"idea_abc": {"risk": 3.0}}
    )
    assert rec.requests[0].url.path == "/skeptic"
    assert rec.sent["scores"] == {"idea_abc": {"risk": 3.0}}
    assert out[0].severity == "major"
    assert out[0].dimension == "competition"


async def test_skeptic_objection_with_a_bad_severity_is_skipped() -> None:
    rec = Rec({"objections": [{**OBJ, "severity": "apocalyptic"}, OBJ]})
    out = await IdeationClient(http(rec)).skeptic("run_1", [IDEA])
    assert len(out) == 1


async def test_suggest_changes_sends_the_delta_and_parses_changes() -> None:
    change = {
        "change_id": "chg_1",
        "text": "Add caching",
        "target_dimension": "feasibility",
        "rationale": "",
        "evidence_ids": [E1],
    }
    rec = Rec({"changes": [change], "discarded": 0, "prompt_version": "v1"})
    out = await IdeationClient(http(rec)).suggest_changes(
        run_id="run_1",
        idea_text="idea",
        dimension="feasibility",
        current=2.0,
        to=3.5,
        evidence=[],
        profile=PROFILE,
    )
    assert rec.requests[0].url.path == "/suggest-changes"
    assert (rec.sent["current_score"], rec.sent["to_score"]) == (2.0, 3.5)
    assert out[0].change_id == "chg_1"


async def test_rewrite_returns_text_and_note() -> None:
    rec = Rec({"new_text": "better idea", "change_note": "added x", "prompt_version": "v1"})
    out = await IdeationClient(http(rec)).rewrite("run_1", "old idea", "add x")
    assert rec.requests[0].url.path == "/rewrite"
    assert rec.sent == {"run_id": "run_1", "idea_text": "old idea", "change_text": "add x"}
    assert (out.new_text, out.change_note) == ("better idea", "added x")


async def test_rewrite_that_returns_nothing_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await IdeationClient(http(Rec({"new_text": ""}))).rewrite("r", "old", "x")


# ---- M7 ------------------------------------------------------------------------------------
async def test_match_niches_translates_to_the_m8_niche_shape() -> None:
    body = {
        "matches": [
            {
                "niche_id": "n1",
                "name": "devtools",
                "status": "rising",
                "size": 10,
                "score": 0.9,
                "matched_terms": [],
            }
        ]
    }
    rec = Rec(body)
    out = await AnalystClient(http(rec)).match_niches(PROFILE, "tools", limit=3)
    assert rec.requests[0].url.path == "/niches/match"
    assert rec.sent == {"profile": PROFILE, "niche_wish": "tools", "limit": 3}
    assert out[0].niche_id == "n1"
    assert out[0].as_m8() == {"name": "devtools", "status": "rising"}


async def test_gaps_are_translated_to_the_m8_gap_shape() -> None:
    body = {
        "gaps": [
            {
                "id": "gap_1",
                "niche_id": "n1",
                "description": "no offline export",
                "evidence_ids": [E1],
                "complaint_count": 3,
                "product_coverage": 0,
                "score": 1.0,
            }
        ]
    }
    rec = Rec(body)
    out = await AnalystClient(http(rec)).gaps("n1")
    assert rec.requests[0].url.path == "/gaps"
    assert dict(rec.requests[0].url.params) == {"niche": "n1"}
    assert out == [
        {"gap_id": "gap_1", "text": "no offline export", "niche": "n1", "evidence_ids": [E1]}
    ]


async def test_gaps_skip_malformed_entries() -> None:
    rec = Rec({"gaps": [{"id": "x"}, {"id": "g", "description": "d", "evidence_ids": [E1]}]})
    out = await AnalystClient(http(rec)).gaps("n1")
    assert [g["gap_id"] for g in out] == ["g"]


# ---- M12 -----------------------------------------------------------------------------------
async def test_filter_objections_maps_accepted_ids_and_quotes() -> None:
    body = {
        "accepted": [
            {
                "objection_id": "obj_1",
                "dimension": "competition",
                "text": "Hadolint exists",
                "evidence_ids": [E1],
                "supporting_quote": "hadolint lints Dockerfiles",
            }
        ],
        "rejected": [{"objection_id": "obj_2", "reason": "quote does not support"}],
    }
    rec = Rec(body)
    out = await CriticClient(http(rec)).filter_objections(
        "run_1", [OBJ, {**OBJ, "objection_id": "obj_2"}]
    )
    assert rec.requests[0].url.path == "/objections/filter"
    first = rec.sent["objections"][0]
    assert first == {
        "objection_id": "obj_1",
        "dimension": "competition",
        "text": "Hadolint exists",
        "evidence_ids": [E1],
    }
    assert out.accepted_quotes == {"obj_1": "hadolint lints Dockerfiles"}
    assert out.rejected == {"obj_2": "quote does not support"}


async def test_filter_objections_truncates_long_claims_to_the_critics_limit() -> None:
    rec = Rec({"accepted": [], "rejected": []})
    await CriticClient(http(rec)).filter_objections("r", [{**OBJ, "claim": "x" * 900}])
    assert len(rec.sent["objections"][0]["text"]) == 600


async def test_filter_with_garbage_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await CriticClient(http(Rec({"accepted": "x"}))).filter_objections("r", [OBJ])


# ---- M2 idea versions (assumed contract, see README) -------------------------------------------
async def test_get_version_returns_text_and_card() -> None:
    rec = Rec({"idea_id": "idea_1", "version": 2, "text": "the idea", "card": {"verdict": "GO"}})
    out = await ProfileClient(http(rec), http(rec)).get_idea_version("idea_1", 2)
    assert rec.requests[0].url.path == "/internal/ideas/idea_1/versions/2"
    assert (out.text, out.card) == ("the idea", {"verdict": "GO"})


async def test_get_version_without_text_is_an_upstream_error() -> None:
    rec = Rec({"version": 2, "card": {}})
    with pytest.raises(UpstreamError):
        await ProfileClient(http(rec), http(rec)).get_idea_version("idea_1", 2)


async def test_missing_version_is_not_found() -> None:
    rec = Rec({}, status=404)
    with pytest.raises(NotFound):
        await ProfileClient(http(rec), http(rec)).get_idea_version("idea_1", 9)


async def test_create_version_sends_text_and_change_note_and_returns_the_number() -> None:
    rec = Rec({"version": 3})
    n = await ProfileClient(http(rec), http(rec)).create_idea_version(
        "idea_1", "new text", "challenge removed cl_7"
    )
    assert rec.requests[0].method == "POST"
    assert rec.requests[0].url.path == "/internal/ideas/idea_1/versions"
    assert rec.sent == {"text": "new text", "change_note": "challenge removed cl_7"}
    assert n == 3


@pytest.mark.parametrize("body", [{}, {"version": 0}, {"version": "3"}, {"version": True}])
async def test_create_version_with_a_bad_number_is_an_upstream_error(body: dict[str, Any]) -> None:
    with pytest.raises(UpstreamError):
        await ProfileClient(http(Rec(body)), http(Rec(body))).create_idea_version("i", "t", "n")


async def test_create_version_rejects_422() -> None:
    with pytest.raises(ValidationFailed):
        await ProfileClient(http(Rec({}, 422)), http(Rec({}))).create_idea_version("i", "t", "n")
