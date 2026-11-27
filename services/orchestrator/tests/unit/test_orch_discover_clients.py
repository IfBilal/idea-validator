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


# ---- M2 idea versions: GET and idempotent PUT --------------------------------------------------
UID = "5f1d2c3e-0000-4000-8000-000000000001"


def m2(rec: Rec) -> ProfileClient:
    return ProfileClient(http(rec), http(rec))


async def test_get_version_sends_the_owner_and_returns_text_card_and_note() -> None:
    body = {
        "idea_id": "idea_1",
        "version": 2,
        "text": "the idea",
        "card": {"verdict": "GO"},
        "change_note": "n",
    }
    rec = Rec(body)
    out = await m2(rec).get_idea_version(UID, "idea_1", 2)
    assert rec.requests[0].url.path == "/internal/ideas/idea_1/versions/2"
    assert dict(rec.requests[0].url.params) == {"user_id": UID}
    assert (out.text, out.card, out.change_note) == ("the idea", {"verdict": "GO"}, "n")


async def test_get_version_with_a_null_card_gives_an_empty_card() -> None:
    out = await m2(Rec({"text": "t", "card": None})).get_idea_version(UID, "idea_1", 1)
    assert out.card == {}


async def test_get_version_falls_back_to_the_verdict_payload_field() -> None:
    rec = Rec({"text": "t", "verdict_payload": {"verdict": "PIVOT"}})
    out = await m2(rec).get_idea_version(UID, "idea_1", 1)
    assert out.card == {"verdict": "PIVOT"}


async def test_get_version_without_text_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        await m2(Rec({"version": 2, "card": {}})).get_idea_version(UID, "idea_1", 2)


async def test_missing_version_is_not_found() -> None:
    with pytest.raises(NotFound):
        await m2(Rec({}, status=404)).get_idea_version(UID, "idea_1", 9)


async def test_a_user_id_with_url_characters_is_encoded() -> None:
    rec = Rec({"text": "t", "card": {}})
    await m2(rec).get_idea_version("a&b=c/d", "idea_1", 1)
    assert dict(rec.requests[0].url.params) == {"user_id": "a&b=c/d"}


async def test_put_version_sends_the_documented_body_and_returns_created() -> None:
    rec = Rec({"idea_id": "idea_1", "version": 3, "created": True})
    created = await m2(rec).put_idea_version(
        user_id=UID,
        idea_id="idea_1",
        version=3,
        text="new text",
        change_note="challenge removed cl_risk",
        card={"verdict": "GO"},
        idempotency_key="run_1",
        base_version=2,
    )
    assert created is True
    assert rec.requests[0].method == "PUT"
    assert rec.requests[0].url.path == "/internal/ideas/idea_1/versions/3"
    assert dict(rec.requests[0].url.params) == {}
    assert rec.sent == {
        "user_id": UID,
        "text": "new text",
        "change_note": "challenge removed cl_risk",
        "card": {"verdict": "GO"},
        "idempotency_key": "run_1",
        "base_version": 2,
    }


async def test_put_version_replay_reports_created_false() -> None:
    rec = Rec({"idea_id": "idea_1", "version": 3, "created": False})
    assert await put(m2(rec)) is False


def put(client: ProfileClient, **over: Any) -> Any:
    args: dict[str, Any] = {
        "user_id": UID,
        "idea_id": "idea_1",
        "version": 3,
        "text": "t",
        "change_note": "n",
        "card": {},
        "idempotency_key": "run_1",
        "base_version": 2,
    }
    return client.put_idea_version(**{**args, **over})


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"idea_id": "idea_1", "version": 3},
        {"idea_id": "idea_1", "version": 3, "created": "yes"},
        {"idea_id": "other", "version": 3, "created": True},
        {"idea_id": "idea_1", "version": 4, "created": True},
        [],
    ],
)
async def test_put_version_with_a_bad_reply_is_an_upstream_error(body: Any) -> None:
    with pytest.raises(UpstreamError):
        await put(m2(Rec(body)))


async def test_put_version_409_is_a_conflict_and_is_never_retried() -> None:
    from common.errors import Conflict

    rec = Rec({}, 409)
    with pytest.raises(Conflict):
        await put(m2(rec))
    assert len(rec.requests) == 1


async def test_put_version_422_is_a_validation_error() -> None:
    with pytest.raises(ValidationFailed):
        await put(m2(Rec({}, 422)))


async def test_put_version_is_retried_on_503_with_the_same_key() -> None:
    calls: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) < 3:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"idea_id": "idea_1", "version": 3, "created": True})

    client = ProfileClient(http(handler), http(handler))
    assert await put(client) is True
    assert [c["idempotency_key"] for c in calls] == ["run_1"] * 3


# ---- M8 views reject refs that are unsafe in paths and keys ------------------------------------------
@pytest.mark.parametrize("bad", ["a/b", "../x", "", "x" * 65, "a b", "idea\n1"])
async def test_an_idea_with_an_unsafe_ref_is_skipped(bad: str) -> None:
    rec = Rec({"ideas": [{**IDEA, "idea_ref": bad}, IDEA], "round": 1})
    out = await IdeationClient(http(rec)).ideate(
        run_id="r", profile=PROFILE, niches=[], gaps=[], round_=1, previous_objections=[], count=5
    )
    assert [i.idea_ref for i in out.ideas] == ["idea_abc"]
    assert out.skipped == 1


@pytest.mark.parametrize("field", ["objection_id", "idea_ref"])
@pytest.mark.parametrize("bad", ["a/b", "../x", "", "x" * 65])
async def test_an_objection_with_an_unsafe_id_is_skipped(field: str, bad: str) -> None:
    rec = Rec({"objections": [{**OBJ, field: bad}, OBJ]})
    out = await IdeationClient(http(rec)).skeptic("run_1", [IDEA])
    assert len(out) == 1
