"""Gap Analyst (M8-04): complaints and missing features per idea, every item cited."""

from typing import Any

from pydantic import ValidationError

from common.errors import DomainError
from common.logging import get_logger
from common.schemas import EvidenceItem

from ..clients.analyst import AnalystClient
from ..clients.kb import KnowledgeBaseClient
from ..config import Settings
from ..models.api import GapAnalystRequest, GapAnalystResponse
from ..models.ideas import Finding, Idea, RawFinding
from .common import Query, envelope_list, gather_evidence, idea_view, keep_ids, normalise
from .prompts import render_evidence, safe_json
from .runner import AgentRunner

AGENT = "gap_analyst"


class GapAnalystService:
    def __init__(
        self,
        runner: AgentRunner,
        kb: KnowledgeBaseClient,
        analyst: AnalystClient | None,
        settings: Settings,
    ) -> None:
        self._runner = runner
        self._kb = kb
        self._analyst = analyst
        self._settings = settings
        self._log = get_logger("ideation.gap_analyst")

    async def analyse(self, request: GapAnalystRequest) -> GapAnalystResponse:
        version = self._runner.prompt_version(AGENT)
        evidence = await self._evidence(request)
        if not evidence:  # evidence or abstain: nothing to cite, so nothing to ask the model
            return GapAnalystResponse(ideas=list(request.ideas), prompt_version=version)

        values = {
            "ideas_json": safe_json([idea_view(i) for i in request.ideas]),
            "evidence_blocks": render_evidence(evidence),
        }
        entries = await self._runner.run(AGENT, request.run_id, values, envelope_list("ideas"))
        if entries is None:
            return GapAnalystResponse(ideas=list(request.ideas), prompt_version=version)

        shown = {e.evidence_id for e in evidence}
        by_ref = {i.idea_ref: i for i in request.ideas}
        found: dict[str, tuple[list[Finding], list[Finding]]] = {}
        discarded = 0
        for entry in entries:
            ref = entry.get("idea_ref") if isinstance(entry, dict) else None
            if ref not in by_ref or ref in found:
                continue
            complaints, lost_a = self._findings(entry.get("complaints"), shown)
            missing, lost_b = self._findings(entry.get("missing_features"), shown)
            found[ref] = (complaints, missing)
            discarded += lost_a + lost_b

        ideas = [
            i.model_copy(
                update={
                    "complaints": found[i.idea_ref][0],
                    "missing_features": found[i.idea_ref][1],
                }
            )
            if i.idea_ref in found
            else i
            for i in request.ideas
        ]
        self._log.info("gap_analyst_done", run_id=request.run_id, discarded=discarded)
        return GapAnalystResponse(ideas=ideas, discarded=discarded, prompt_version=version)

    def _findings(self, raw_list: Any, shown: set[str]) -> tuple[list[Finding], int]:
        kept: list[Finding] = []
        seen: set[str] = set()
        discarded = 0
        for raw in raw_list if isinstance(raw_list, list) else []:
            try:
                parsed = RawFinding.model_validate(raw)
            except ValidationError:
                discarded += 1
                continue
            ids = keep_ids(parsed.evidence_ids, shown)
            key = normalise(parsed.text)
            if not ids or key in seen:
                discarded += 1
                continue
            if len(kept) >= self._settings.max_items_per_idea:
                discarded += 1
                continue
            seen.add(key)
            kept.append(Finding(text=parsed.text, evidence_ids=ids))  # type: ignore[arg-type]
        return kept, discarded

    async def _evidence(self, request: GapAnalystRequest) -> list[EvidenceItem]:
        k = self._settings.search_k
        queries: list[Query] = []
        for idea in request.ideas:
            queries.append((f"{idea.title} {idea.summary}", ["complaint", "request"]))
            queries.append((f"{idea.title}: {'; '.join(idea.core_features)}", ["product"]))
        items = await gather_evidence(
            self._kb, request.run_id, queries, k=k, cap=self._settings.max_evidence_items
        )
        known = {i.evidence_id for i in items}
        for gap_item in await self._m7_gap_evidence(request.ideas):
            if gap_item.evidence_id not in known and len(items) < self._settings.max_evidence_items:
                items.append(gap_item)
        return items

    async def _m7_gap_evidence(self, ideas: list[Idea]) -> list[EvidenceItem]:
        """Evidence behind M7's known gaps for the ideas' niches. Optional: failures are logged."""
        if self._analyst is None:
            return []
        niches = list(dict.fromkeys(i.niche for i in ideas if i.niche))
        ids: list[str] = []
        try:
            for niche in niches:
                for gap in await self._analyst.get_gaps(niche):
                    ids.extend(gap.evidence_ids)
            return list((await self._kb.existing_evidence(ids)).values())
        except DomainError as exc:
            self._log.warning("analyst_unavailable", error=exc.message)
            return []
