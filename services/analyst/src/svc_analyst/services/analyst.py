"""M7 orchestration: recompute niches/gaps from M6 cards, answer queries, compute features.

No web framework here. Everything is deterministic: the only inputs are the cards, the request
and the config (no clock: dates are relative to the data's own ``as_of``).
"""

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from common.errors import DomainError, NotFound, UpstreamError
from common.logging import get_logger
from common.schemas.profile import StudentProfile

from ..models.api import FeaturesRequest, FeaturesResponse, NicheMatch, RecomputeResponse
from ..models.cards import PRODUCT_KINDS, Card
from ..models.domain import Gap, Niche, Snapshot
from ..models.ports import CardSource, EvidenceSearch, NicheStore
from . import features as feat
from .clustering import ClusterConfig, analyse, dedupe_cards
from .gaps import GapConfig, detect_gaps
from .text import TfidfEmbedder, Vector, centroid, cosine, jaccard, tokenize

log = get_logger("analyst")

DEFAULT_IN_DEMAND: tuple[str, ...] = (
    "python",
    "typescript",
    "javascript",
    "react",
    "sql",
    "postgresql",
    "docker",
    "kubernetes",
    "aws",
    "machine learning",
    "llm",
    "fastapi",
    "pytorch",
)
_STATUS_BONUS = {"rising": 0.2, "flat": 0.0, "saturated": -0.2}


@dataclass(frozen=True)
class AnalystConfig:
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    gaps: GapConfig = field(default_factory=GapConfig)
    as_of: date | None = None  # overrides the KB snapshot date
    relevance_tau: float | None = None  # drop evidence scored below this (unscored is kept)
    in_demand_skills: tuple[str, ...] = DEFAULT_IN_DEMAND
    search_k: int = 30
    niche_min_similarity: float = 0.05  # idea -> niche fallback when evidence names no niche
    interest_weight: float = 0.6
    skill_weight: float = 0.2


@dataclass(frozen=True)
class _Product:
    card: Card
    vector: Vector
    core_tokens: frozenset[str]
    users: frozenset[str]


@dataclass(frozen=True)
class _Index:
    snapshot: Snapshot
    cards: dict[str, Card]
    embedder: TfidfEmbedder
    vectors: dict[str, Vector]
    products: list[_Product]
    niches: dict[str, Niche]
    niche_of: dict[str, str]
    niche_centroids: dict[str, Vector]
    gap_vectors: dict[str, Vector]
    niche_terms: dict[str, frozenset[str]]


def _core_text(card: Card) -> str:
    return f"{card.name} {card.summary or card.text[:300]}"


def _build_index(cards: Sequence[Card], snapshot: Snapshot) -> _Index:
    ordered = sorted(cards, key=lambda c: c.id)
    documents = [c.document for c in ordered]
    embedder = TfidfEmbedder(documents)
    vectors = dict(zip((c.id for c in ordered), embedder.vectorize(documents), strict=True))
    by_id = {c.id: c for c in ordered}
    niche_of = {cid: n.id for n in snapshot.niches for cid in n.card_ids}
    gap_vectors: dict[str, Vector] = {
        g.id: centroid(
            [vectors[e.split("#")[0]] for e in g.evidence_ids if e.split("#")[0] in vectors]
        )
        for g in snapshot.gaps
    }
    return _Index(
        snapshot=snapshot,
        cards=by_id,
        embedder=embedder,
        vectors=vectors,
        products=[
            _Product(
                c,
                vectors[c.id],
                frozenset(tokenize(_core_text(c))),
                feat.target_users(f"for {c.customer}") if c.customer else frozenset(),
            )
            for c in ordered
            if c.kind in PRODUCT_KINDS
        ],
        niches={n.id: n for n in snapshot.niches},
        niche_of=niche_of,
        niche_centroids={
            n.id: centroid([vectors[i] for i in n.card_ids if i in vectors])
            for n in snapshot.niches
        },
        gap_vectors=gap_vectors,
        niche_terms={n.id: frozenset(n.keywords) for n in snapshot.niches},
    )


class AnalystService:
    def __init__(
        self,
        cards: CardSource,
        store: NicheStore,
        *,
        search: EvidenceSearch | None = None,
        config: AnalystConfig | None = None,
    ) -> None:
        self._cards_source = cards
        self._store = store
        self._search = search
        self._config = config or AnalystConfig()
        self._index: _Index | None = None
        self._inflight: asyncio.Task[RecomputeResponse] | None = None

    @property
    def ready(self) -> bool:
        return self._index is not None

    # ---- lifecycle ---------------------------------------------------------------------------

    async def recompute(self) -> RecomputeResponse:
        """Re-read M6, re-cluster, re-detect gaps, store and swap the index.

        Single flight: callers that arrive while a run is in progress await that same run
        instead of starting another. The CPU-bound work runs in a worker thread, so the event
        loop (probes, other requests) stays responsive. In production the same code is meant to
        be driven by a recompute Job, with replicas only serving the stored result.
        """
        task = self._inflight
        if task is None or task.done():
            task = asyncio.create_task(self._recompute())
            self._inflight = task
            task.add_done_callback(self._clear_inflight)
        return await asyncio.shield(task)

    def _clear_inflight(self, task: "asyncio.Task[RecomputeResponse]") -> None:
        if self._inflight is task:
            self._inflight = None
        if not task.cancelled():
            task.exception()  # mark retrieved: callers that went away must not log a warning

    async def _as_of(self) -> date:
        as_of = self._config.as_of or await self._cards_source.snapshot_date()
        if as_of is None:
            raise UpstreamError("knowledge base reported no snapshot date (set AS_OF)")
        return as_of

    async def _recompute(self) -> RecomputeResponse:
        cards = await self._cards_source.list_cards()
        as_of = await self._as_of()
        stored = await self._store.load()
        previous = stored.niches if stored else []
        snapshot, index, read = await asyncio.to_thread(self._compute, cards, as_of, previous)
        await self._store.save(snapshot)
        self._index = index
        log.info(
            "analysis_recomputed",
            cards=read,
            niches=len(snapshot.niches),
            gaps=len(snapshot.gaps),
            unassigned=len(snapshot.unassigned),
            ignored_future=len(snapshot.ignored_future),
        )
        return RecomputeResponse(
            cards=read,
            niches=len(snapshot.niches),
            gaps=len(snapshot.gaps),
            unassigned=len(snapshot.unassigned),
            ignored_future=len(snapshot.ignored_future),
            as_of=snapshot.as_of,
        )

    def _compute(
        self, cards: Sequence[Card], as_of: date, previous: Sequence[Niche]
    ) -> tuple[Snapshot, _Index, int]:
        """Pure CPU work (runs in a thread): cluster, find gaps, build the lookup index."""
        unique = dedupe_cards(cards)
        analysis = analyse(unique, self._config.cluster, as_of=as_of, previous=previous)
        skipped = set(analysis.ignored_future)
        valid = [c for c in unique if c.id not in skipped]
        gaps = detect_gaps(analysis.niches, valid, as_of, self._config.gaps)
        snapshot = Snapshot(
            as_of=as_of,
            niches=analysis.niches,
            gaps=gaps,
            unassigned=analysis.unassigned,
            ignored_future=analysis.ignored_future,
        )
        return snapshot, _build_index(valid, snapshot), len(unique)

    async def ensure(self) -> _Index:
        """Recompute once if there is no analysis yet (safe under concurrency)."""
        if self._index is None:
            await self.recompute()
        assert self._index is not None
        return self._index

    # ---- queries -----------------------------------------------------------------------------

    async def list_niches(self, status: str | None) -> list[Niche]:
        index = await self.ensure()
        niches = index.snapshot.niches
        return [n for n in niches if status is None or n.status == status]

    async def get_niche(self, niche_id: str) -> Niche:
        index = await self.ensure()
        try:
            return index.niches[niche_id]
        except KeyError:
            raise NotFound(f"niche {niche_id} not found") from None

    async def list_gaps(self, niche_id: str | None) -> list[Gap]:
        index = await self.ensure()
        return [g for g in index.snapshot.gaps if niche_id is None or g.niche_id == niche_id]

    async def match(
        self, profile: StudentProfile, wish: str | None, limit: int
    ) -> list[NicheMatch]:
        """Rank niches: ``0.6 * interest overlap + 0.2 * skill overlap + status bonus``.

        interest overlap = share of the query terms (wish + interests) among the niche's
        keywords; skill overlap likewise for ``profile.skills``; bonus rising +0.2, saturated -0.2.
        Ties break on niche id.
        """
        index = await self.ensure()
        cfg = self._config
        query = set(tokenize(wish or ""))
        for interest in profile.interests:
            query.update(tokenize(interest))
        skills = {t for s in profile.skills for t in tokenize(s)}
        matches: list[NicheMatch] = []
        for niche in index.snapshot.niches:
            terms = index.niche_terms[niche.id]
            hit = sorted(query & terms)
            interest_share = len(hit) / len(query) if query else 0.0
            skill_share = len(skills & terms) / len(skills) if skills else 0.0
            score = (
                cfg.interest_weight * interest_share
                + cfg.skill_weight * skill_share
                + _STATUS_BONUS[niche.status]
            )
            matches.append(
                NicheMatch(
                    niche_id=niche.id,
                    name=niche.name,
                    status=niche.status,
                    size=niche.size,
                    score=round(score, 4),
                    matched_terms=hit,
                )
            )
        matches.sort(key=lambda m: (-m.score, m.niche_id))
        return matches[:limit]

    # ---- features ----------------------------------------------------------------------------

    async def _evidence(
        self,
        index: _Index,
        evidence_ids: Sequence[str],
        scores: dict[str, float] | None,
    ) -> tuple[list[feat.EvidenceRef], int]:
        """Resolve cited ids to cards. Returns the refs and how many ids M6 does not know."""
        unique = list(dict.fromkeys(evidence_ids))
        wanted = {e.split("#")[0] for e in unique}
        missing = sorted(wanted - index.cards.keys())
        fetched = await self._cards_source.get_cards(missing) if missing else {}
        as_of = index.snapshot.as_of
        refs: list[feat.EvidenceRef] = []
        rejected = 0
        for evidence_id in unique:
            card_id = evidence_id.split("#")[0]
            card = index.cards.get(card_id) or fetched.get(card_id)
            if card is None or (as_of and card.published_at and card.published_at > as_of):
                log.warning("evidence_rejected", evidence_id=evidence_id)
                rejected += 1
                continue
            refs.append(feat.EvidenceRef.of(card, evidence_id, (scores or {}).get(evidence_id)))
        return refs, rejected

    async def _candidates(self, index: _Index, idea_text: str) -> list[_Product]:
        if self._search is None:
            return index.products
        try:
            hits = await self._search.search(
                idea_text, k=self._config.search_k, kinds=sorted(PRODUCT_KINDS)
            )
        except DomainError:
            log.warning("search_unavailable_using_all_products", exc_info=True)
            return index.products
        found = {h.card_id for h in hits}
        return [p for p in index.products if p.card.id in found]

    def _niche_for(
        self, index: _Index, refs: Sequence[feat.EvidenceRef], idea: Vector
    ) -> Niche | None:
        votes = Counter(index.niche_of[r.card_id] for r in refs if r.card_id in index.niche_of)
        if votes:
            best = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            return index.niches[best]
        ranked = sorted(
            ((cosine(idea, c), nid) for nid, c in index.niche_centroids.items()),
            key=lambda pair: (-pair[0], pair[1]),
        )
        if ranked and ranked[0][0] >= self._config.niche_min_similarity:
            return index.niches[ranked[0][1]]
        return None

    async def features(self, request: FeaturesRequest) -> FeaturesResponse:
        index = await self.ensure()
        cfg = self._config
        text = request.idea_text
        profile = request.profile
        found, rejected = await self._evidence(index, request.evidence_ids, request.evidence_scores)
        refs = feat.relevant(found, cfg.relevance_tau)

        (idea_vec,) = index.embedder.vectorize([text])
        idea_tokens = frozenset(tokenize(text))
        idea_users = feat.target_users(text)
        scored = [
            feat.Scored(
                card=p.card,
                cosine=round(cosine(idea_vec, p.vector), 6),
                jaccard=round(jaccard(idea_tokens, p.core_tokens), 6),
                same_user=feat.same_user(idea_users, p.users),
            )
            for p in await self._candidates(index, text)
        ]

        niche = self._niche_for(index, refs, idea_vec)
        sizes = [n.size for n in index.snapshot.niches]
        niche_gaps = [
            g for g in index.snapshot.gaps if niche is not None and g.niche_id == niche.id
        ]
        relevance = {g.id: cosine(idea_vec, index.gap_vectors[g.id]) for g in niche_gaps}

        required = (
            sorted({s.strip().lower() for s in request.required_skills if s.strip()})
            if request.required_skills is not None
            else feat.extract_required_skills(text, profile.skills)
        )
        overlap = feat.skill_overlap(profile.skills, required)
        scope = request.scope or feat.infer_scope(text)
        trend = feat.trend_growth(niche.trend_slope if niche else None)
        as_of = index.snapshot.as_of

        flags = feat.risk_flags(text)
        out = FeaturesResponse(
            n_sources=feat.n_sources(refs),
            n_platforms=feat.n_platforms(refs),
            complaint_count=feat.complaint_count(refs),
            trend_growth=round(trend, 4),
            recency_share=round(feat.recency_share(refs, as_of), 4) if as_of else 0.0,
            n_competitors=feat.n_competitors(scored),
            competitor_strength=round(feat.competitor_strength(scored), 4),
            niche_saturation=round(
                feat.niche_saturation(
                    niche.status if niche else None, niche.size if niche else None, sizes
                ),
                4,
            ),
            max_similarity=round(feat.max_similarity(scored), 4),
            unaddressed_gap_count=feat.unaddressed_gap_count(
                niche_gaps, relevance, {r.evidence_id for r in refs}
            ),
            skill_overlap=round(overlap, 4),
            available_hours=feat.available_hours(
                profile.hours_per_week, profile.weeks_available, profile.team_size
            ),
            estimated_effort_hours=round(
                feat.estimated_effort_hours(scope, overlap, feat.needs_gpu(text), profile.compute),
                2,
            ),
            compute_fit=feat.compute_fit(feat.required_compute(text), profile.compute),
            risk_flags=flags,
            platform_dependency=feat.platform_dependency(text),
            skill_novelty=feat.skill_novelty(required, profile.skills, profile.interests),
            cv_signal=round(feat.cv_signal(required, cfg.in_demand_skills, trend), 4),
            required_skills=required,
            rejected_evidence_count=rejected,
            duplicate_of=feat.duplicate_of(scored),
            risk_blocker=feat.risk_blocker(flags),
        )
        log.info(
            "features_computed",
            evidence=len(refs),
            niche=niche.id if niche else None,
            competitors=out.n_competitors,
        )
        return out


__all__ = ["AnalystConfig", "AnalystService"]
