"""Ideator (M8-01..03): propose ideas from gaps, then enforce the rules in code, not by trust.

Pipeline per proposal: schema -> duplicate title -> known gap -> evidence exists -> fits the
student's hours -> not a near-copy of an existing product -> count cap. Anything the model adds
beyond the schema (verdict, score, decision ...) is discarded by the ``RawIdea`` parser.
"""

from typing import Any

from pydantic import ValidationError

from common.logging import get_logger
from common.schemas import EvidenceItem

from ..clients.kb import KnowledgeBaseClient
from ..config import Settings
from ..models.api import DroppedIdea, Gap, IdeateRequest, IdeateResponse
from ..models.ideas import Idea, RawIdea
from .fit import fits_profile
from .prompts import render_evidence, safe_json
from .runner import AgentRunner
from .similarity import feature_overlap

AGENT = "ideator"
NO_GAPS = "no_gaps_with_evidence"
NO_IDEAS = "no_valid_ideas"
BAD_OUTPUT = "invalid_model_output"


def _envelope(data: dict[str, Any]) -> list[Any] | None:
    ideas = data.get("ideas")
    return ideas if isinstance(ideas, list) else None


class IdeatorService:
    def __init__(self, runner: AgentRunner, kb: KnowledgeBaseClient, settings: Settings) -> None:
        self._runner = runner
        self._kb = kb
        self._settings = settings
        self._log = get_logger("ideation.ideator")

    async def ideate(self, request: IdeateRequest) -> IdeateResponse:
        version = self._runner.prompt_version(AGENT)

        def result(
            ideas: list[Idea] | None = None,
            dropped: list[DroppedIdea] | None = None,
            reason: str | None = None,
        ) -> IdeateResponse:
            return IdeateResponse(
                ideas=ideas or [],
                dropped=dropped or [],
                reason=reason if not ideas else None,
                round=request.round,
                prompt_version=version,
            )

        gaps = await self._resolve_gaps(request.gaps)
        if not gaps:
            return result(reason=NO_GAPS)

        proposals = await self._runner.run(
            AGENT, request.run_id, self._values(request, gaps), _envelope
        )
        if proposals is None:
            return result(reason=BAD_OUTPUT)

        ideas, dropped = await self._enforce(request, proposals, gaps)
        self._log.info("ideate_done", run_id=request.run_id, kept=len(ideas), dropped=len(dropped))
        return result(ideas, dropped, NO_IDEAS)

    # -- input side -------------------------------------------------------------------------
    async def _resolve_gaps(self, gaps: list[Gap]) -> dict[str, tuple[Gap, list[EvidenceItem]]]:
        """Gaps with usable evidence only: provided texts, plus ids fetched from M6."""
        wanted: list[str] = []
        for gap in gaps:
            provided = {e.evidence_id for e in gap.evidence}
            wanted.extend(i for i in gap.evidence_ids if i not in provided)
        fetched = await self._kb.existing_evidence(wanted)

        resolved: dict[str, tuple[Gap, list[EvidenceItem]]] = {}
        budget = self._settings.max_evidence_items
        for gap in gaps:
            items = {e.evidence_id: e for e in gap.evidence}
            for evidence_id in gap.evidence_ids:
                if evidence_id not in items and evidence_id in fetched:
                    items[evidence_id] = fetched[evidence_id]
            usable = list(items.values())[:budget]
            if usable and gap.gap_id not in resolved:
                budget -= len(usable)
                resolved[gap.gap_id] = (gap, usable)
            if budget <= 0:
                break
        return resolved

    def _values(
        self, request: IdeateRequest, gaps: dict[str, tuple[Gap, list[EvidenceItem]]]
    ) -> dict[str, str]:
        blocks = [
            f"GAP {safe_json(gap.gap_id)} (niche: {safe_json(gap.niche)}): "
            f"{safe_json(gap.text)}\n{render_evidence(items)}"
            for gap, items in gaps.values()
        ]
        return {
            "count": str(min(request.count, self._settings.max_ideas)),
            "round": str(request.round),
            "profile_json": safe_json(request.profile.model_dump(mode="json")),
            "niches_json": safe_json([n.model_dump(mode="json") for n in request.niches]),
            "gaps_blocks": "\n\n".join(blocks),
            "objections_json": safe_json(
                [o.model_dump(mode="json") for o in request.previous_objections]
            ),
        }

    # -- output side ------------------------------------------------------------------------
    async def _enforce(
        self,
        request: IdeateRequest,
        proposals: list[Any],
        gaps: dict[str, tuple[Gap, list[EvidenceItem]]],
    ) -> tuple[list[Idea], list[DroppedIdea]]:
        dropped: list[DroppedIdea] = []
        candidates: list[tuple[RawIdea, Gap]] = []
        seen_refs: set[str] = set()
        limit = min(request.count, self._settings.max_ideas)

        for raw in proposals:
            try:
                idea = RawIdea.model_validate(raw)
            except ValidationError:
                title = raw.get("title") if isinstance(raw, dict) else None
                dropped.append(
                    DroppedIdea(title=str(title or "(unparseable)")[:200], reason="invalid_schema")
                )
                continue
            ref = Idea.from_raw(idea).idea_ref
            if ref in seen_refs:
                dropped.append(DroppedIdea(title=idea.title, reason="duplicate_title"))
            elif idea.gap_ref not in gaps:
                dropped.append(DroppedIdea(title=idea.title, reason="unknown_gap"))
            else:
                seen_refs.add(ref)
                candidates.append((idea, gaps[idea.gap_ref][0]))

        known = {i.evidence_id for _, items in gaps.values() for i in items}
        extra = [i for idea, _ in candidates for i in idea.gap_evidence_ids if i not in known]
        existing = set(await self._kb.existing_evidence(extra))

        kept: list[Idea] = []
        for raw_idea, gap in candidates:
            if len(kept) >= limit:
                break
            ids = [i for i in raw_idea.gap_evidence_ids if i in known or i in existing]
            if not ids:
                dropped.append(DroppedIdea(title=raw_idea.title, reason="unsupported_evidence"))
                continue
            if not fits_profile(raw_idea.est_scope, request.profile):
                dropped.append(DroppedIdea(title=raw_idea.title, reason="exceeds_available_hours"))
                continue
            clean = raw_idea.model_copy(update={"gap_evidence_ids": ids})
            rival = await self._near_duplicate(request.run_id, clean)
            if rival is not None:
                dropped.append(
                    DroppedIdea(title=clean.title, reason="near_duplicate", detail=rival)
                )
                continue
            kept.append(Idea.from_raw(clean, gap.niche))
        return kept, dropped

    async def _near_duplicate(self, run_id: str, idea: RawIdea) -> str | None:
        """Name of an existing product whose feature text overlaps too much, else None."""
        query = f"{idea.title}: {'; '.join(idea.core_features)}"
        products = await self._kb.search(
            query, k=self._settings.search_k, run_id=run_id, kinds=["product"]
        )
        for product in products:
            score = feature_overlap(idea.core_features, product.quote)
            if score >= self._settings.similarity_threshold:
                name = product.card_name or product.card_id
                return f"similar to {name} (feature overlap {score:.2f})"
        return None
