"""AnalystService: recompute, niche queries, matching and /features, against in-memory fakes."""

import asyncio
from collections.abc import Sequence

import pytest

from common.errors import NotFound, UpstreamError
from common.schemas.profile import StudentProfile
from svc_analyst.clients.fakes import InMemoryCardSource, InMemoryEvidenceSearch
from svc_analyst.fixtures import build_cards
from svc_analyst.models.api import FeaturesRequest
from svc_analyst.models.cards import Card, EvidenceHit
from svc_analyst.repositories.memory import InMemoryNicheStore
from svc_analyst.services.analyst import AnalystConfig, AnalystService

CARDS = build_cards()
FEATURE_NAMES = {
    "n_sources",
    "n_platforms",
    "complaint_count",
    "trend_growth",
    "recency_share",
    "n_competitors",
    "competitor_strength",
    "niche_saturation",
    "max_similarity",
    "unaddressed_gap_count",
    "skill_overlap",
    "available_hours",
    "estimated_effort_hours",
    "compute_fit",
    "risk_flags",
    "platform_dependency",
    "skill_novelty",
    "cv_signal",
    "required_skills",
}


def profile(**kw: object) -> StudentProfile:
    base: dict[str, object] = {
        "user_id": "u1",
        "skills": ["python", "react"],
        "hours_per_week": 10,
        "weeks_available": 8,
        "team_size": 2,
        "compute": "laptop",
        "goal": "portfolio",
        "interests": ["invoicing"],
    }
    base.update(kw)
    return StudentProfile.model_validate(base)


class CountingSource(InMemoryCardSource):
    def __init__(self, cards: Sequence[Card]) -> None:
        super().__init__(cards)
        self.list_calls = 0
        self.fail = False

    async def list_cards(self) -> list[Card]:
        self.list_calls += 1
        await asyncio.sleep(0)
        if self.fail:
            raise UpstreamError("kb down")
        return await super().list_cards()


class FailingSearch:
    async def search(
        self, query: str, *, k: int = 10, kinds: Sequence[str] | None = None
    ) -> list[EvidenceHit]:
        raise UpstreamError("search down")


def service(
    cards: Sequence[Card] = CARDS,
    *,
    search: object = "default",
    store: InMemoryNicheStore | None = None,
) -> AnalystService:
    engine = InMemoryEvidenceSearch(cards) if search == "default" else search
    return AnalystService(
        CountingSource(cards),
        store or InMemoryNicheStore(),
        search=engine,  # type: ignore[arg-type]
        config=AnalystConfig(),
    )


def invoice_evidence() -> list[str]:
    return [
        c.evidence_id
        for c in CARDS
        if "invoicing" in c.niche_tags and c.is_complaint and c.published_at
    ][:6]


def req(text: str, evidence: list[str] | None = None, **kw: object) -> FeaturesRequest:
    return FeaturesRequest.model_validate(
        {
            "idea_text": text,
            "evidence_ids": evidence if evidence is not None else [],
            "profile": profile(**kw).model_dump(),
        }
    )


# ---- recompute / lifecycle ---------------------------------------------------------------------


async def test_recompute_summarises_and_stores() -> None:
    store = InMemoryNicheStore()
    svc = service(store=store)
    summary = await svc.recompute()
    assert summary.cards == len(CARDS)
    assert summary.niches == 6
    assert summary.gaps >= 6
    assert summary.unassigned == 0
    snapshot = await store.load()
    assert snapshot is not None and len(snapshot.niches) == 6


async def test_ensure_recomputes_once_even_under_concurrency() -> None:
    svc = service()
    source = svc._cards_source
    await asyncio.gather(*(svc.ensure() for _ in range(5)))
    assert source.list_calls == 1  # type: ignore[attr-defined]
    await svc.ensure()
    assert source.list_calls == 1  # type: ignore[attr-defined]


async def test_ensure_can_retry_after_the_knowledge_base_failed() -> None:
    svc = service()
    source = svc._cards_source
    source.fail = True  # type: ignore[attr-defined]
    with pytest.raises(UpstreamError):
        await svc.ensure()
    assert svc.ready is False
    source.fail = False  # type: ignore[attr-defined]
    await svc.ensure()
    assert svc.ready is True


async def test_recompute_replaces_the_previous_snapshot() -> None:
    svc = service()
    await svc.recompute()
    first = [n.id for n in await svc.list_niches(None)]
    await svc.recompute()
    assert [n.id for n in await svc.list_niches(None)] == first


async def test_empty_knowledge_base_gives_empty_but_ready_service() -> None:
    svc = service([])
    summary = await svc.recompute()
    assert (summary.cards, summary.niches, summary.gaps) == (0, 0, 0)
    assert svc.ready is True
    assert await svc.list_niches(None) == []
    assert await svc.list_gaps(None) == []
    features = await svc.features(req("an invoice tool"))
    assert features.n_sources == 0
    assert features.niche_saturation == 0.5


# ---- queries ---------------------------------------------------------------------------------


async def test_list_niches_filters_by_status() -> None:
    svc = service()
    everything = await svc.list_niches(None)
    assert len(everything) == 6
    assert {n.status for n in everything} == {"rising", "flat", "saturated"}
    for status in ("rising", "flat", "saturated"):
        listed = await svc.list_niches(status)
        assert listed and all(n.status == status for n in listed)
        assert len(listed) == sum(1 for n in everything if n.status == status)


async def test_get_niche_returns_series_and_unknown_is_not_found() -> None:
    svc = service()
    niche = (await svc.list_niches(None))[0]
    detail = await svc.get_niche(niche.id)
    assert detail.id == niche.id
    assert detail.series
    with pytest.raises(NotFound):
        await svc.get_niche("niche_00000000")


async def test_list_gaps_all_and_by_niche() -> None:
    svc = service()
    gaps = await svc.list_gaps(None)
    niche = (await svc.list_niches(None))[0]
    mine = await svc.list_gaps(niche.id)
    assert mine and all(g.niche_id == niche.id for g in mine)
    assert len(mine) < len(gaps)
    assert await svc.list_gaps("niche_00000000") == []
    assert all(g.evidence_ids for g in gaps)


# ---- match -----------------------------------------------------------------------------------


async def test_match_ranks_the_niche_named_in_the_wish_first() -> None:
    svc = service()
    ranked = await svc.match(profile(interests=[]), "invoice billing for freelancers", 5)
    assert "invoice" in ranked[0].name
    assert ranked[0].score > ranked[1].score
    assert "invoice" in ranked[0].matched_terms


async def test_match_uses_interests_and_skills() -> None:
    svc = service()
    ranked = await svc.match(profile(interests=["vocabulary", "language"], skills=[]), None, 3)
    assert "language" in ranked[0].name
    ranked = await svc.match(profile(interests=[], skills=["gpu", "model"]), None, 3)
    assert "gpu" in ranked[0].name or "model" in ranked[0].name


async def test_match_status_bonus_breaks_otherwise_equal_niches() -> None:
    svc = service()
    ranked = await svc.match(profile(interests=[], skills=[]), None, 20)
    statuses = [m.status for m in ranked]
    assert statuses.index("rising") < statuses.index("saturated")
    scores = [m.score for m in ranked]
    assert scores == sorted(scores, reverse=True)
    assert {m.score for m in ranked if m.status == "rising"} == {0.2}
    assert {m.score for m in ranked if m.status == "saturated"} == {-0.2}


async def test_match_limit_determinism_and_unknown_terms() -> None:
    svc = service()
    one = await svc.match(profile(), "invoice", 2)
    two = await svc.match(profile(), "invoice", 2)
    assert len(one) == 2
    assert one == two
    nothing = await svc.match(profile(interests=[], skills=[]), "zzzzqqq", 20)
    assert all(m.matched_terms == [] for m in nothing)
    assert len(await svc.match(profile(), None, 100)) == 6


# ---- features --------------------------------------------------------------------------------


async def test_features_returns_exactly_the_documented_names() -> None:
    svc = service()
    out = await svc.features(
        req("an invoice reminder tool for freelance designers", invoice_evidence())
    )
    assert set(out.model_dump()) == FEATURE_NAMES


async def test_features_from_evidence() -> None:
    svc = service()
    evidence = invoice_evidence()
    out = await svc.features(req("an invoice reminder tool for freelance designers", evidence))
    by_id = {c.id: c for c in CARDS}
    cards = {e.split("#")[0] for e in evidence}
    assert out.n_sources == len(cards)
    assert out.complaint_count == len(evidence)
    assert out.n_platforms == len(set().union(*(by_id[c].all_platforms for c in cards)))
    assert 0.0 <= out.recency_share <= 1.0
    assert out.recency_share > 0.5  # invoicing evidence is recent
    assert out.trend_growth > 0.10  # the invoicing niche is rising
    assert out.available_hours == 160
    assert out.unaddressed_gap_count >= 1


async def test_features_with_no_evidence_are_zeroes_and_still_computed() -> None:
    svc = service()
    out = await svc.features(req("a simple todo list", []))
    assert (out.n_sources, out.n_platforms, out.complaint_count, out.recency_share) == (
        0,
        0,
        0,
        0.0,
    )
    assert out.risk_flags == []
    assert out.platform_dependency is False


async def test_unknown_evidence_ids_are_ignored() -> None:
    svc = service()
    evidence = [*invoice_evidence()[:2], "card_deadbeef#c0"]
    out = await svc.features(req("an invoice tool", evidence))
    assert out.n_sources == len({e.split("#")[0] for e in evidence[:2]})


async def test_duplicate_evidence_ids_are_counted_once() -> None:
    svc = service()
    one = invoice_evidence()[0]
    out = await svc.features(req("an invoice tool", [one, one, one]))
    assert out.n_sources == 1
    assert out.complaint_count == 1


async def test_competitors_and_similarity_against_the_kb() -> None:
    svc = service()
    product = next(c for c in CARDS if c.is_product and "invoicing" in c.niche_tags)
    clone = f"{product.name}: {product.summary}. {product.text}"
    out = await svc.features(req(clone, []))
    assert out.max_similarity >= 0.8
    assert out.n_competitors >= 1
    assert out.competitor_strength >= 0.0
    unrelated = await svc.features(req("quantum teleporter cheese", []))
    assert unrelated.max_similarity < 0.2
    assert unrelated.n_competitors == 0
    assert unrelated.competitor_strength == 0.0


async def test_competitors_use_search_candidates_and_fall_back_when_search_fails() -> None:
    product = next(c for c in CARDS if c.is_product and "invoicing" in c.niche_tags)
    clone = f"{product.name}: {product.summary}. {product.text}"
    with_search = await service().features(req(clone, []))
    broken = await service(search=FailingSearch()).features(req(clone, []))
    without = await service(search=None).features(req(clone, []))
    assert with_search.max_similarity == broken.max_similarity == without.max_similarity


async def test_target_user_must_match_for_a_competitor() -> None:
    product = next(c for c in CARDS if c.is_product and "invoicing" in c.niche_tags)
    assert product.customer is not None
    clone = f"{product.name}: {product.summary}. {product.text}".replace(product.customer, "")
    same = await service().features(req(f"{clone} built for freelance designers", []))
    other = await service().features(req(f"{clone} built for astronauts", []))
    assert same.n_competitors >= 1
    assert other.n_competitors == 0


async def test_profile_drives_hours_effort_and_compute() -> None:
    svc = service()
    text = "fine-tune a small LLM with CUDA in Python for students"
    laptop = await svc.features(req(text, [], compute="laptop", skills=["python"]))
    gpu = await svc.features(req(text, [], compute="free_gpu", skills=["python"]))
    assert laptop.compute_fit is False
    assert gpu.compute_fit is True
    assert laptop.estimated_effort_hours == pytest.approx(gpu.estimated_effort_hours * 1.5)
    assert "python" in laptop.required_skills
    assert "llm" in laptop.required_skills
    assert laptop.skill_overlap == 0.5  # python yes, llm no
    novelty = await svc.features(req(text, [], skills=["python"], interests=[]))
    assert novelty.skill_novelty == 1  # llm is new to this student


async def test_explicit_required_skills_and_scope_override_the_heuristics() -> None:
    svc = service()
    request = FeaturesRequest.model_validate(
        {
            "idea_text": "a todo list",
            "evidence_ids": [],
            "profile": profile(skills=["python"]).model_dump(),
            "required_skills": ["Python", "Rust"],
            "scope": "L",
        }
    )
    out = await svc.features(request)
    assert out.required_skills == ["python", "rust"]
    assert out.skill_overlap == 0.5
    assert out.estimated_effort_hours == pytest.approx(400 * 1.25)


async def test_risk_flags_and_platform_dependency_flow_through() -> None:
    svc = service()
    out = await svc.features(
        req("scrape LinkedIn behind login to track patient data via Slack", [])
    )
    assert "blocker" in out.risk_flags
    assert "health_data" in out.risk_flags
    assert out.platform_dependency is True


async def test_cv_signal_uses_the_niche_trend_and_the_in_demand_list() -> None:
    svc = service()
    rising = await svc.features(
        req("an invoice tool in Python and React for freelancers", invoice_evidence())
    )
    cold = await svc.features(req("a tool in cobol", []))
    assert rising.cv_signal > cold.cv_signal
    assert 0.0 <= cold.cv_signal <= 1.0


async def test_features_are_deterministic() -> None:
    svc = service()
    request = req("an invoice reminder tool for freelance designers", invoice_evidence())
    a = await svc.features(request)
    b = await service().features(request)
    assert a.model_dump_json() == b.model_dump_json()


async def test_evidence_not_in_the_index_is_fetched_from_the_source() -> None:
    late = CARDS[0].model_copy(update={"id": "card_0badcafe"})
    source = CountingSource([*CARDS])
    svc = AnalystService(source, InMemoryNicheStore(), search=None, config=AnalystConfig())
    await svc.recompute()
    # a card created in M6 after the last recompute
    extra = InMemoryCardSource([late])
    svc._cards_source = _Both(source, extra)
    out = await svc.features(req("an invoice tool", ["card_0badcafe#c0"]))
    assert out.n_sources == 1


class _Both:
    def __init__(self, a: InMemoryCardSource, b: InMemoryCardSource) -> None:
        self.a, self.b = a, b

    async def list_cards(self) -> list[Card]:
        return await self.a.list_cards()

    async def get_cards(self, ids: Sequence[str]) -> dict[str, Card]:
        return {**await self.a.get_cards(ids), **await self.b.get_cards(ids)}
