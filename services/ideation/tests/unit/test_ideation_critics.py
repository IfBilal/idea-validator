"""Gap Analyst and Skeptic: evidence-backed outputs, unsupported items dropped in code."""

import json
from typing import Any

import pytest
from ideation_fakes import (
    PROMPTS_DIR,
    FakeAnalyst,
    FakeKb,
    FakeLlm,
    eid,
    evidence,
    profile,
    raw_idea,
)

from common.errors import UpstreamError
from svc_ideation.config import Settings
from svc_ideation.models.api import GapAnalystRequest, SkepticRequest
from svc_ideation.models.ideas import Idea, RawIdea
from svc_ideation.services.gap_analyst import GapAnalystService
from svc_ideation.services.runner import AgentRunner
from svc_ideation.services.skeptic import SkepticService

E1 = evidence(1, 1, "export is painfully slow")
E2 = evidence(2, 1, "no offline mode, users complain")
E3 = evidence(3, 1, "RivalApp launched with the same features")


def idea(**over: Any) -> Idea:
    return Idea.from_raw(RawIdea.model_validate(raw_idea(**over)), niche="edtech")


def runner(llm: FakeLlm) -> AgentRunner:
    return AgentRunner(llm.client(), PROMPTS_DIR, max_tokens=800)


def kb_with(*items: Any) -> FakeKb:
    return FakeKb(known=list(items), search=lambda _b: list(items))


def gap_service(
    llm: FakeLlm, kb: FakeKb, analyst: FakeAnalyst | None = None, **cfg: Any
) -> GapAnalystService:
    return GapAnalystService(
        runner(llm), kb.client(), analyst.client() if analyst else None, Settings(**cfg)
    )


def skeptic_service(llm: FakeLlm, kb: FakeKb, **cfg: Any) -> SkepticService:
    return SkepticService(runner(llm), kb.client(), Settings(**cfg))


def gap_request(*ideas: Idea) -> GapAnalystRequest:
    return GapAnalystRequest(run_id="run_1", ideas=list(ideas) or [idea()])


def gap_reply(ref: str, complaints: list[Any], missing: list[Any]) -> dict[str, Any]:
    return {"ideas": [{"idea_ref": ref, "complaints": complaints, "missing_features": missing}]}


def item(text: str, *ids: str) -> dict[str, Any]:
    return {"text": text, "evidence_ids": list(ids)}


# ----------------------------------------------------------------------------- Gap Analyst
async def test_gap_analyst_enriches_idea_with_cited_items():
    i = idea()
    llm = FakeLlm(gap_reply(i.idea_ref, [item("slow export", eid(1))], [item("offline", eid(2))]))
    result = await gap_service(llm, kb_with(E1, E2)).analyse(gap_request(i))
    [out] = result.ideas
    assert out.complaints[0].text == "slow export"
    assert out.complaints[0].evidence_ids == [eid(1)]
    assert out.missing_features[0].evidence_ids == [eid(2)]
    assert out.title == i.title  # the idea itself is unchanged
    assert result.prompt_version == "gap_analyst@v1"
    assert llm.requests[0]["temperature"] == 0.0


async def test_gap_analyst_searches_complaints_and_products_and_wraps_evidence():
    kb = kb_with(E1)
    llm = FakeLlm(gap_reply(idea().idea_ref, [], []))
    await gap_service(llm, kb).analyse(gap_request())
    kinds = [s["filters"].get("kinds") for s in kb.searches]
    assert ["complaint", "request"] in kinds
    assert ["product"] in kinds
    assert '<evidence id="card_00000001#c1"' in llm.requests[0]["messages"][1]["content"]


async def test_gap_analyst_drops_items_without_valid_evidence():
    i = idea()
    reply = gap_reply(
        i.idea_ref,
        [item("cited", eid(1)), item("no ids"), item("invented", eid(99)), item("bad id", "x")],
        [item("also invented", eid(98), eid(97))],
    )
    result = await gap_service(FakeLlm(reply), kb_with(E1)).analyse(gap_request(i))
    [out] = result.ideas
    assert [c.text for c in out.complaints] == ["cited"]
    assert out.missing_features == []
    assert result.discarded == 4


async def test_gap_analyst_keeps_only_shown_ids_inside_a_mixed_item():
    i = idea()
    reply = gap_reply(i.idea_ref, [item("mixed", eid(1), eid(99))], [])
    result = await gap_service(FakeLlm(reply), kb_with(E1)).analyse(gap_request(i))
    assert result.ideas[0].complaints[0].evidence_ids == [eid(1)]


@pytest.mark.parametrize(("n", "kept"), [(3, 3), (4, 4), (5, 4), (9, 4)])
async def test_gap_analyst_caps_items_per_list(n: int, kept: int):
    i = idea()
    complaints = [item(f"complaint {k}", eid(1)) for k in range(n)]
    llm = FakeLlm(gap_reply(i.idea_ref, complaints, []))
    result = await gap_service(llm, kb_with(E1)).analyse(gap_request(i))
    assert len(result.ideas[0].complaints) == kept


async def test_gap_analyst_deduplicates_identical_items():
    i = idea()
    reply = gap_reply(i.idea_ref, [item("Slow export", eid(1)), item(" slow  export", eid(1))], [])
    result = await gap_service(FakeLlm(reply), kb_with(E1)).analyse(gap_request(i))
    assert len(result.ideas[0].complaints) == 1


async def test_gap_analyst_ignores_unknown_idea_refs_and_keeps_unmentioned_ideas():
    a, b = idea(title="Alpha idea"), idea(title="Beta idea")
    reply = {
        "ideas": [
            {"idea_ref": "idea_ghost", "complaints": [item("x", eid(1))], "missing_features": []},
            {"idea_ref": a.idea_ref, "complaints": [item("slow", eid(1))], "missing_features": []},
        ]
    }
    result = await gap_service(FakeLlm(reply), kb_with(E1)).analyse(gap_request(a, b))
    assert [i.title for i in result.ideas] == ["Alpha idea", "Beta idea"]
    assert len(result.ideas[0].complaints) == 1
    assert result.ideas[1].complaints == []


async def test_gap_analyst_discards_verdict_and_score_keys():
    i = idea()
    reply = {
        "ideas": [
            {
                "idea_ref": i.idea_ref,
                "verdict": "GO",
                "score": 5,
                "complaints": [{"text": "slow", "evidence_ids": [eid(1)], "verdict": "GO"}],
                "missing_features": [],
            }
        ],
        "decision": "build",
    }
    result = await gap_service(FakeLlm(reply), kb_with(E1)).analyse(gap_request(i))
    dumped = json.dumps(result.model_dump(mode="json"))
    assert "GO" not in dumped
    assert "verdict" not in dumped and "decision" not in dumped


async def test_gap_analyst_invalid_json_keeps_ideas_unenriched_after_one_retry():
    llm = FakeLlm("nope")
    result = await gap_service(llm, kb_with(E1)).analyse(gap_request())
    assert result.ideas[0].complaints == []
    assert len(llm.requests) == 2


async def test_gap_analyst_without_any_evidence_abstains_without_a_model_call():
    llm = FakeLlm(gap_reply("x", [], []))
    result = await gap_service(llm, FakeKb()).analyse(gap_request())
    assert llm.requests == []
    assert result.ideas[0].complaints == []


async def test_gap_analyst_adds_m7_gap_evidence_for_the_ideas_niche():
    analyst = FakeAnalyst({"edtech": [{"gap_id": "g", "text": "t", "evidence_ids": [eid(2)]}]})
    kb = FakeKb(known=[E1, E2], search=lambda _b: [E1])
    i = idea()
    llm = FakeLlm(gap_reply(i.idea_ref, [], [item("offline", eid(2))]))
    result = await gap_service(llm, kb, analyst).analyse(gap_request(i))
    assert analyst.calls == ["edtech"]
    assert result.ideas[0].missing_features[0].evidence_ids == [eid(2)]


async def test_gap_analyst_survives_an_analyst_outage():
    analyst = FakeAnalyst()
    analyst.fail = True
    i = idea()
    llm = FakeLlm(gap_reply(i.idea_ref, [item("slow", eid(1))], []))
    result = await gap_service(llm, kb_with(E1), analyst).analyse(gap_request(i))
    assert len(result.ideas[0].complaints) == 1


async def test_gap_analyst_fails_loudly_when_the_knowledge_base_is_down():
    kb = kb_with(E1)
    kb.fail = True
    with pytest.raises(UpstreamError):
        await gap_service(FakeLlm("{}"), kb).analyse(gap_request())


async def test_gap_analyst_caps_evidence_in_the_prompt():
    many = [evidence(n, 1, f"chunk {n}") for n in range(1, 30)]
    llm = FakeLlm(gap_reply(idea().idea_ref, [], []))
    await gap_service(llm, kb_with(*many), max_evidence_items=5).analyse(gap_request())
    assert llm.requests[0]["messages"][1]["content"].count("<evidence id=") == 5


# ----------------------------------------------------------------------------- Skeptic
def objection(
    ref: str, dim: str = "competition", sev: str = "major", *ids: str, **over: Any
) -> Any:
    data = {
        "idea_ref": ref,
        "dimension": dim,
        "claim": f"claim about {dim}",
        "severity": sev,
        "evidence_ids": list(ids or [eid(3)]),
    }
    data.update(over)
    return data


def skeptic_request(*ideas: Idea, **over: Any) -> SkepticRequest:
    return SkepticRequest(run_id="run_1", ideas=list(ideas) or [idea()], **over)


async def test_skeptic_returns_objections_with_generated_ids_and_evidence():
    i = idea()
    llm = FakeLlm({"objections": [objection(i.idea_ref)]})
    result = await skeptic_service(llm, kb_with(E3)).challenge(skeptic_request(i))
    [obj] = result.objections
    assert obj.objection_id.startswith("obj_")
    assert obj.idea_ref == i.idea_ref
    assert obj.dimension == "competition"
    assert obj.severity == "major"
    assert obj.evidence_ids == [eid(3)]
    assert llm.requests[0]["temperature"] == 0.0
    assert result.prompt_version == "skeptic@v1"


async def test_skeptic_objection_ids_are_deterministic_and_model_ids_ignored():
    i = idea()
    raw = objection(i.idea_ref, objection_id="obj_model_chosen")
    first = await skeptic_service(FakeLlm({"objections": [raw]}), kb_with(E3)).challenge(
        skeptic_request(i)
    )
    second = await skeptic_service(FakeLlm({"objections": [raw]}), kb_with(E3)).challenge(
        skeptic_request(i)
    )
    assert first.objections[0].objection_id == second.objections[0].objection_id
    assert first.objections[0].objection_id != "obj_model_chosen"


async def test_skeptic_drops_objections_whose_evidence_is_not_in_m6():
    i = idea()
    objs = [
        objection(i.idea_ref, "competition", "major", eid(3)),
        objection(i.idea_ref, "risk", "minor", eid(99)),
        objection(i.idea_ref, "learning", "minor"),
    ]
    objs[2]["evidence_ids"] = []
    result = await skeptic_service(FakeLlm({"objections": objs}), kb_with(E3)).challenge(
        skeptic_request(i)
    )
    assert [o.dimension for o in result.objections] == ["competition"]
    assert result.discarded == 2


async def test_skeptic_accepts_an_id_that_exists_in_m6_even_if_not_in_the_search_results():
    i = idea()
    kb = FakeKb(known=[E3, E2], search=lambda _b: [E3])
    llm = FakeLlm({"objections": [objection(i.idea_ref, "demand_pain", "minor", eid(2))]})
    result = await skeptic_service(llm, kb).challenge(skeptic_request(i))
    assert len(result.objections) == 1


@pytest.mark.parametrize(("n", "kept"), [(1, 1), (3, 3), (4, 3), (6, 3)])
async def test_skeptic_caps_objections_per_idea(n: int, kept: int):
    i = idea()
    dims = ["demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"]
    objs = [objection(i.idea_ref, dims[k], "minor") for k in range(n)]
    result = await skeptic_service(FakeLlm({"objections": objs}), kb_with(E3)).challenge(
        skeptic_request(i)
    )
    assert len(result.objections) == kept


async def test_skeptic_cap_keeps_the_most_severe_first():
    i = idea()
    objs = [
        objection(i.idea_ref, "risk", "minor"),
        objection(i.idea_ref, "learning", "minor"),
        objection(i.idea_ref, "feasibility", "minor"),
        objection(i.idea_ref, "competition", "fatal"),
    ]
    result = await skeptic_service(FakeLlm({"objections": objs}), kb_with(E3)).challenge(
        skeptic_request(i)
    )
    assert result.objections[0].severity == "fatal"
    assert len(result.objections) == 3


async def test_skeptic_cap_is_per_idea():
    a, b = idea(title="Alpha idea"), idea(title="Beta idea")
    dims = ["risk", "learning", "feasibility", "competition"]
    objs = [objection(r, d, "minor") for r in (a.idea_ref, b.idea_ref) for d in dims]
    result = await skeptic_service(FakeLlm({"objections": objs}), kb_with(E3)).challenge(
        skeptic_request(a, b)
    )
    assert len(result.objections) == 6


@pytest.mark.parametrize(
    "bad",
    [
        {"dimension": "vibes"},
        {"severity": "apocalyptic"},
        {"claim": ""},
        {"idea_ref": "idea_ghost"},
        {"dimension": None},
    ],
)
async def test_skeptic_drops_malformed_objections(bad: dict[str, Any]):
    i = idea()
    llm = FakeLlm({"objections": [objection(i.idea_ref, **bad), objection(i.idea_ref, "risk")]})
    result = await skeptic_service(llm, kb_with(E3)).challenge(skeptic_request(i))
    assert [o.dimension for o in result.objections] == ["risk"]
    assert result.discarded == 1


async def test_skeptic_never_emits_verdicts_or_scores():
    i = idea()
    raw = objection(i.idea_ref, verdict="NO-GO", score=1.0, decision="stop", recommendation="no")
    llm = FakeLlm({"objections": [raw], "verdict": "NO-GO", "overall_score": 0})
    result = await skeptic_service(llm, kb_with(E3)).challenge(skeptic_request(i))
    dumped = json.dumps(result.model_dump(mode="json"))
    for forbidden in ("verdict", "NO-GO", "score", "decision", "recommendation"):
        assert forbidden not in dumped


async def test_skeptic_duplicate_objections_collapse():
    i = idea()
    raw = objection(i.idea_ref)
    result = await skeptic_service(FakeLlm({"objections": [raw, raw]}), kb_with(E3)).challenge(
        skeptic_request(i)
    )
    assert len(result.objections) == 1


async def test_skeptic_invalid_json_returns_no_objections_after_one_retry():
    llm = FakeLlm("not json")
    result = await skeptic_service(llm, kb_with(E3)).challenge(skeptic_request())
    assert result.objections == []
    assert len(llm.requests) == 2


async def test_skeptic_includes_profile_scores_and_wrapped_evidence_in_the_prompt():
    i = idea()
    llm = FakeLlm({"objections": []})
    req = skeptic_request(
        i,
        profile=profile(),
        scores={i.idea_ref: {"competition": 1.5, "risk": 4.0}},
    )
    await skeptic_service(llm, kb_with(E3)).challenge(req)
    user = llm.requests[0]["messages"][1]["content"]
    assert "1.5" in user
    assert "hours_per_week" in user
    assert '<evidence id="card_00000003#c1"' in user
    assert "RivalApp launched" in user


def test_skeptic_scores_must_use_known_dimensions_and_range():
    with pytest.raises(ValueError):
        skeptic_request(scores={"idea_x": {"vibes": 1.0}})  # type: ignore[dict-item]


async def test_skeptic_fails_loudly_when_the_knowledge_base_is_down():
    kb = kb_with(E3)
    kb.fail = True
    with pytest.raises(UpstreamError):
        await skeptic_service(FakeLlm("{}"), kb).challenge(skeptic_request())
