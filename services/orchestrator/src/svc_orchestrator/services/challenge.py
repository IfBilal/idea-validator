"""The Challenge run (docs/harness/06 §4): load, check, new version, recompute, finish.

The claim is re-checked against its evidence by M12. Supported means defended and nothing changes.
Unsupported means the claim is removed: its dimension abstains, M2 creates a new idea version,
and M11 (never this code) recomputes the verdict for it.
"""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from common.errors import Conflict, NotFound, UpstreamError
from common.schemas import DimensionScore

from ..models.challenge_state import ChallengeState
from ..models.state import RunStatus
from . import builders
from .guard import guard
from .run_context import RunContext

Update = dict[str, Any]
Route = Callable[[ChallengeState], str]


# ---- pure routing ----
def after_load(state: ChallengeState) -> str:
    return "finish" if state.stopped else "check"


def after_check(state: ChallengeState) -> str:
    if state.stopped or state.outcome != "removed":
        return "finish"
    return "version"


def after_version(state: ChallengeState) -> str:
    return "finish" if state.stopped else "recompute"


def after_recompute(state: ChallengeState) -> str:
    return "finish"


EDGES: dict[str, tuple[Route, list[str]]] = {
    "load": (after_load, ["check", "finish"]),
    "check": (after_check, ["version", "finish"]),
    "version": (after_version, ["recompute", "finish"]),
    "recompute": (after_recompute, ["finish"]),
}
STEP_NAMES = {
    "load": "LOAD",
    "check": "CHECK",
    "version": "NEW_VERSION",
    "recompute": "RECOMPUTE",
    "finish": "FINISH",
}


def removal_request(
    verdict_input: dict[str, Any], dimension: str, *, version: int, run_id: str
) -> dict[str, Any]:
    """The recorded M11 input with one dimension abstained and the new version number."""
    scores = [DimensionScore.model_validate(s) for s in verdict_input["scores"]]
    results = [
        {"claim_id": f"cl_{s.dimension}", "supported": s.dimension != dimension} for s in scores
    ]
    abstained, _ = builders.apply_claim_results(scores, results)
    return {
        **verdict_input,
        "version": version,
        "run_id": run_id,
        "scores": [s.model_dump(mode="json") for s in abstained],
    }


class ChallengeNodes:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.t = ctx.config.timeouts

    def _step(self, state: ChallengeState, step: str, **kw: Any) -> Any:
        return self.ctx.runner.run(state.memo, step, **kw)

    async def load(self, state: ChallengeState) -> Update:
        profile = self.ctx.clients.profile

        async def call() -> Any:
            found = await profile.get_idea_version(state.idea_id, state.version)
            return {"text": found.text, "card": found.card}

        out = await self._step(
            state,
            "challenge_load",
            tool="m2.get_idea_version",
            inputs={"idea_id": state.idea_id, "version": state.version},
            call=call,
            timeout_s=self.t.profile,
            memoise=False,
        )
        card = out.value["card"]
        if card.get("partial") or card.get("verdict") is None:
            raise Conflict("only a settled verdict card can be challenged")
        claim = next(
            (c for c in card.get("claims") or [] if c.get("claim_id") == state.claim_id), None
        )
        if claim is None:
            raise NotFound(f"claim {state.claim_id} is not on this card")
        if claim.get("removed"):
            raise Conflict(f"claim {state.claim_id} was already removed")
        return {"text": out.value["text"], "base_card": card, "claim": claim}

    async def check(self, state: ChallengeState) -> Update:
        claim = state.claim or {}
        sent = {
            "claim_id": claim["claim_id"],
            "text": claim["text"],
            "evidence_ids": claim["evidence_ids"],
            "dimension": claim.get("dimension"),
        }

        async def call() -> Any:
            response = await self.ctx.clients.critic.check(self.ctx.run_id, [sent])
            return [r.model_dump(mode="json") for r in response.results]

        out = await self._step(
            state,
            "challenge_check",
            tool="m12.check",
            inputs=sent,
            call=call,
            timeout_s=self.t.critic,
        )
        result = next((r for r in out.value if r["claim_id"] == state.claim_id), None)
        if result is not None and result["supported"]:
            return {
                "outcome": "defended",
                "reason": result["reason"],
                "supporting_quote": result.get("supporting_quote"),
                "memo": out.memo_update,
            }
        # no answer for the claim counts as unsupported: fail closed
        reason = result["reason"] if result else "the critic returned no result for this claim"
        return {"outcome": "removed", "reason": reason, "memo": out.memo_update}

    async def version(self, state: ChallengeState) -> Update:
        card = state.base_card or {}
        if not isinstance(card.get("verdict_input"), dict):
            raise Conflict("the card has no recorded M11 inputs, so it cannot be recomputed")
        profile = self.ctx.clients.profile
        note = f"challenge removed {state.claim_id}"

        async def call() -> Any:
            return await profile.create_idea_version(state.idea_id, state.text or "", note)

        out = await self._step(
            state,
            "challenge_version",
            tool="m2.create_idea_version",
            inputs={"idea_id": state.idea_id, "note": note},
            call=call,
            timeout_s=self.t.profile,
            memoise=False,
        )
        return {"new_version": out.value}

    async def recompute(self, state: ChallengeState) -> Update:
        base = state.base_card or {}
        claim = state.claim or {}
        if state.new_version is None:
            raise Conflict("no new version was created")
        request = removal_request(
            base["verdict_input"],
            str(claim["dimension"]),
            version=state.new_version,
            run_id=self.ctx.run_id,
        )

        async def call() -> Any:
            verdict = await self.ctx.clients.verdict.verdict(request)
            return verdict.model_dump(mode="json", by_alias=True)

        out = await self._step(
            state,
            "challenge_verdict",
            tool="m11.verdict",
            inputs=request,
            call=call,
            timeout_s=self.t.verdict,
        )
        verdict = out.value
        if verdict.get("idea_id") != state.idea_id or verdict.get("version") != state.new_version:
            raise UpstreamError("M11 returned a verdict for a different idea or version")
        claims = [
            {**c, "removed": c.get("removed", False) or c.get("claim_id") == state.claim_id}
            for c in base.get("claims") or []
        ]
        log = [
            *(base.get("challenge_log") or []),
            {"claim_id": state.claim_id, "outcome": "removed", "reason": state.reason},
        ]
        card = {
            **verdict,
            "run_id": self.ctx.run_id,
            "claims": claims,
            "challenge_log": log,
            "cache_hit": False,
            "evidence": base.get("evidence") or {},
            "notes": [f"challenge removed {state.claim_id}"],
            "degraded": False,
            "partial": False,
            "needs_input": [],
            "changes": [],
            "verdict_input": request,
            "change_note": f"challenge removed {state.claim_id}",
            "base_version": state.version,
        }
        return {"new_card": card, "memo": out.memo_update}

    async def finish(self, state: ChallengeState) -> Update:
        status = state.status if state.stopped else RunStatus.SUCCEEDED
        claim = state.claim or {}
        result: dict[str, Any] = {"claim_id": state.claim_id}
        if state.outcome is not None:
            result.update(
                {
                    "outcome": state.outcome,
                    "reason": state.reason,
                    "evidence_ids": list(claim.get("evidence_ids") or []),
                }
            )
        if state.outcome == "defended":
            result["supporting_quote"] = state.supporting_quote
        if state.outcome == "removed":
            result["new_version"] = state.new_version
            result["card"] = state.new_card
        if status is not RunStatus.SUCCEEDED:
            result["partial"] = True
            result["stop_reason"] = state.stop_reason
            result["error"] = state.error
        if status is RunStatus.SUCCEEDED:
            if state.new_card is not None:
                await self.ctx.emitter.emit("verdict", card=state.new_card)
            await self.ctx.emitter.emit("run_completed", status="succeeded")
        elif status is RunStatus.BUDGET_STOPPED:
            await self.ctx.emitter.emit("budget_stopped", reason=state.stop_reason)
        else:
            await self.ctx.emitter.emit("run_failed", status="failed", reason=state.error)
        return {"card": result, "status": status}


def build_challenge_graph(
    ctx: RunContext, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[ChallengeState, None, ChallengeState, ChallengeState]:
    nodes = ChallengeNodes(ctx)
    graph = StateGraph(ChallengeState)
    for name, step in STEP_NAMES.items():
        graph.add_node(name, guard(ctx, step, getattr(nodes, name)))
    graph.add_edge(START, "load")
    for name, (route, targets) in EDGES.items():
        graph.add_conditional_edges(name, route, targets)
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
