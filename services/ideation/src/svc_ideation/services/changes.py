"""Change Suggester (U7): turn an M11 dimension delta into concrete, cited idea changes.

Text only: the output never carries a score or verdict, and the target dimension is the one in the
request, whatever the model says.
"""

from pydantic import ValidationError

from common.logging import get_logger

from ..clients.kb import KnowledgeBaseClient
from ..config import Settings
from ..models.api import SuggestChangesRequest, SuggestChangesResponse
from ..models.ideas import Change, RawChange, stable_id
from .common import envelope_list, gather_evidence, keep_ids, normalise
from .prompts import render_evidence, safe_json
from .runner import AgentRunner

AGENT = "change_suggester"


class ChangeService:
    def __init__(self, runner: AgentRunner, kb: KnowledgeBaseClient, settings: Settings) -> None:
        self._runner = runner
        self._kb = kb
        self._settings = settings
        self._log = get_logger("ideation.changes")

    async def suggest(self, request: SuggestChangesRequest) -> SuggestChangesResponse:
        version = self._runner.prompt_version(AGENT)
        run_id = request.run_id or "run_unknown"
        evidence = list(request.evidence)
        if not evidence:
            query = f"{request.idea_text[:300]} {request.target_dimension}"
            evidence = await gather_evidence(
                self._kb,
                run_id,
                [(query, None)],
                k=self._settings.search_k,
                cap=self._settings.max_evidence_items,
            )
        if not evidence:  # evidence or abstain
            return SuggestChangesResponse(changes=[], prompt_version=version)

        values = {
            "target_dimension": request.target_dimension,
            "from_score": f"{request.current_score:.1f}",
            "to_score": f"{request.target_score:.1f}",
            "idea_text": safe_json(request.idea_text),
            "profile_json": safe_json(
                request.profile.model_dump(mode="json") if request.profile else None
            ),
            "evidence_blocks": render_evidence(evidence),
        }
        entries = await self._runner.run(AGENT, run_id, values, envelope_list("changes"))
        if entries is None:
            return SuggestChangesResponse(changes=[], prompt_version=version)

        shown = {e.evidence_id for e in evidence}
        parsed: list[RawChange] = []
        discarded = 0
        for entry in entries:
            try:
                parsed.append(RawChange.model_validate(entry))
            except ValidationError:
                discarded += 1
        allowed = shown | set(
            await self._kb.existing_evidence(
                [i for raw in parsed for i in raw.evidence_ids if i not in shown]
            )
        )

        changes: list[Change] = []
        seen: set[str] = set()
        for raw in parsed:
            ids = keep_ids(raw.evidence_ids, allowed)
            key = normalise(raw.text)
            if not ids or key in seen or len(changes) >= self._settings.max_changes:
                discarded += 1
                continue
            seen.add(key)
            changes.append(
                Change(
                    change_id=stable_id("chg", request.target_dimension, key),
                    text=raw.text,
                    target_dimension=request.target_dimension,
                    rationale=raw.rationale,
                    evidence_ids=ids,
                )
            )
        self._log.info("changes_done", run_id=run_id, kept=len(changes), discarded=discarded)
        return SuggestChangesResponse(changes=changes, discarded=discarded, prompt_version=version)
