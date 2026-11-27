from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from common.errors import ValidationFailed
from svc_critic.models.evidence import EvidenceRecord
from svc_critic.models.objections import Objection, ObjectionFilterRequest
from svc_critic.services.claims import ClaimChecker
from svc_critic.services.entailment import LexicalOverlapJudge
from svc_critic.services.objections import ObjectionFilter

E1 = "card_8f21aaaa#c1"
E2 = "card_8f21aaaa#c2"
MISSING = "card_deadbeef#c9"


class FakeSource:
    def __init__(self) -> None:
        self.records = {
            E1: EvidenceRecord(
                evidence_id=E1,
                card_name="Study Planner",
                text="Students struggle to plan weekly study schedules.",
            ),
            E2: EvidenceRecord(
                evidence_id=E2, card_name="Notion", text="Notion is expensive for students."
            ),
        }

    async def fetch_evidence(self, ids: Sequence[str]) -> dict[str, EvidenceRecord]:
        return {i: self.records[i] for i in ids if i in self.records}


def make_filter(cap: int = 5) -> ObjectionFilter:
    return ObjectionFilter(ClaimChecker(FakeSource(), LexicalOverlapJudge()), max_accepted=cap)


def obj(oid: str, text: str, ids: list[str], dim: str | None = "demand_pain") -> Objection:
    return Objection(objection_id=oid, text=text, evidence_ids=ids, dimension=dim)


async def run(f: ObjectionFilter, *objections: Objection):  # type: ignore[no-untyped-def]
    return await f.filter(ObjectionFilterRequest(run_id="r1", objections=list(objections)))


async def test_backed_objection_is_accepted_with_its_quote() -> None:
    out = await run(make_filter(), obj("o1", "Students struggle with study schedules", [E1]))
    assert [a.objection_id for a in out.accepted] == ["o1"]
    assert out.accepted[0].supporting_quote == "Students struggle to plan weekly study schedules."
    assert out.accepted[0].dimension == "demand_pain"
    assert out.rejected == []


async def test_objection_without_evidence_is_rejected_with_reason() -> None:
    out = await run(make_filter(), obj("o1", "Nobody wants this", []))
    assert out.accepted == []
    assert [(r.objection_id, r.reason) for r in out.rejected] == [("o1", "no evidence cited")]


async def test_objection_with_unknown_evidence_is_rejected() -> None:
    out = await run(make_filter(), obj("o1", "Students struggle with schedules", [E1, MISSING]))
    assert out.rejected[0].reason == "evidence id missing"


async def test_objection_whose_evidence_does_not_back_it_is_rejected() -> None:
    out = await run(make_filter(), obj("o1", "Market is saturated with free options", [E1]))
    assert out.rejected[0].reason == "quote does not support"


async def test_mixed_input_is_partitioned_preserving_order() -> None:
    out = await run(
        make_filter(),
        obj("a", "Students struggle with study schedules", [E1]),
        obj("b", "Totally made up claim here", [MISSING]),
        obj("c", "Notion is expensive for students", [E2], "competition"),
    )
    assert [a.objection_id for a in out.accepted] == ["a", "c"]
    assert [r.objection_id for r in out.rejected] == ["b"]


@pytest.mark.parametrize(
    ("cap", "accepted", "rejected"), [(1, 1, 2), (2, 2, 1), (3, 3, 0), (4, 3, 0)]
)
async def test_cap_boundaries(cap: int, accepted: int, rejected: int) -> None:
    objections = [obj(f"o{i}", "Students struggle with study schedules", [E1]) for i in range(3)]
    out = await run(make_filter(cap), *objections)
    assert len(out.accepted) == accepted
    assert len(out.rejected) == rejected
    assert [a.objection_id for a in out.accepted] == [f"o{i}" for i in range(accepted)]
    assert all(r.reason == "over objection cap" for r in out.rejected)


async def test_cap_only_counts_accepted_objections() -> None:
    out = await run(
        make_filter(1),
        obj("bad", "Totally made up", [MISSING]),
        obj("good", "Students struggle with study schedules", [E1]),
    )
    assert [a.objection_id for a in out.accepted] == ["good"]
    assert [(r.objection_id, r.reason) for r in out.rejected] == [("bad", "evidence id missing")]


async def test_empty_list_is_fine() -> None:
    out = await run(make_filter())
    assert out.accepted == [] and out.rejected == []


async def test_duplicate_objection_ids_are_rejected() -> None:
    with pytest.raises(ValidationFailed):
        await run(make_filter(), obj("x", "one two three", [E1]), obj("x", "four five six", [E1]))


def test_cap_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_accepted"):
        make_filter(0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"objection_id": "", "text": "t", "evidence_ids": []},
        {"objection_id": "o", "text": "", "evidence_ids": []},
        {"objection_id": "o", "text": "x" * 601, "evidence_ids": []},
        {"objection_id": "o", "text": "t", "evidence_ids": ["bad id"]},
        {"objection_id": "o", "text": "t", "evidence_ids": [], "dimension": "nonsense"},
        {"objection_id": "o", "text": "t", "evidence_ids": [], "extra": 1},
    ],
)
def test_objection_model_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Objection(**kwargs)


def test_objection_text_at_max_length_is_valid() -> None:
    Objection(objection_id="o", text="x" * 600, evidence_ids=[])
