"""Skeptic (M8-05): evidence-backed objections per idea and dimension. Never a verdict or a score.

Objections whose evidence ids do not exist in M6 are dropped here; M12 checks again later.
"""

from collections import defaultdict

from pydantic import ValidationError

from common.logging import get_logger

from ..clients.kb import KnowledgeBaseClient
from ..config import Settings
from ..models.api import SkepticRequest, SkepticResponse
from ..models.ideas import Objection, RawObjection, stable_id
from .common import Query, envelope_list, gather_evidence, idea_view, keep_ids
from .prompts import render_evidence, safe_json
from .runner import AgentRunner

AGENT = "skeptic"
SEVERITY_RANK = {"fatal": 0, "major": 1, "minor": 2}


class SkepticService:
    def __init__(self, runner: AgentRunner, kb: KnowledgeBaseClient, settings: Settings) -> None:
        self._runner = runner
        self._kb = kb
        self._settings = settings
        self._log = get_logger("ideation.skeptic")

    async def challenge(self, request: SkepticRequest) -> SkepticResponse:
        version = self._runner.prompt_version(AGENT)
        queries: list[Query] = []
        for idea in request.ideas:
            queries.append((f"{idea.title} competitors alternatives", ["product"]))
            queries.append((f"{idea.title} failure risk problems", None))
        evidence = await gather_evidence(
            self._kb,
            request.run_id,
            queries,
            k=self._settings.search_k,
            cap=self._settings.max_evidence_items,
        )
        scores = request.scores or {}
        values = {
            "ideas_json": safe_json(
                [{**idea_view(i), "scores": scores.get(i.idea_ref)} for i in request.ideas]
            ),
            "profile_json": safe_json(
                request.profile.model_dump(mode="json") if request.profile else None
            ),
            "evidence_blocks": render_evidence(evidence),
        }
        entries = await self._runner.run(AGENT, request.run_id, values, envelope_list("objections"))
        if entries is None:
            return SkepticResponse(objections=[], prompt_version=version)

        refs = {i.idea_ref for i in request.ideas}
        parsed: list[RawObjection] = []
        discarded = 0
        for entry in entries:
            try:
                raw = RawObjection.model_validate(entry)
            except ValidationError:
                discarded += 1
                continue
            if raw.idea_ref in refs:
                parsed.append(raw)
            else:
                discarded += 1

        shown = {e.evidence_id for e in evidence}
        extra = [i for raw in parsed for i in raw.evidence_ids if i not in shown]
        allowed = shown | set(await self._kb.existing_evidence(extra))

        objections: list[Objection] = []
        seen: set[str] = set()
        for raw in parsed:
            ids = keep_ids(raw.evidence_ids, allowed)
            objection_id = stable_id("obj", raw.idea_ref, raw.dimension, raw.claim.lower())
            if not ids or objection_id in seen:
                discarded += 1
                continue
            seen.add(objection_id)
            objections.append(
                Objection(
                    objection_id=objection_id,
                    idea_ref=raw.idea_ref,
                    dimension=raw.dimension,
                    claim=raw.claim,
                    severity=raw.severity,
                    evidence_ids=ids,  # type: ignore[arg-type]
                )
            )

        capped, dropped = self._cap(objections)
        self._log.info(
            "skeptic_done", run_id=request.run_id, kept=len(capped), discarded=discarded + dropped
        )
        return SkepticResponse(
            objections=capped, discarded=discarded + dropped, prompt_version=version
        )

    def _cap(self, objections: list[Objection]) -> tuple[list[Objection], int]:
        """At most N per idea, most severe first (stable for equal severity)."""
        grouped: dict[str, list[Objection]] = defaultdict(list)
        for obj in objections:
            grouped[obj.idea_ref].append(obj)
        kept: list[Objection] = []
        for items in grouped.values():
            items.sort(key=lambda o: SEVERITY_RANK[o.severity])
            kept.extend(items[: self._settings.max_objections_per_idea])
        return kept, len(objections) - len(kept)
