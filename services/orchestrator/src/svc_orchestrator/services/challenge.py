"""The Challenge run (docs/harness/06 §4): load, check, recompute, save, finish.

The claim is re-checked against its evidence by M12. Supported means defended and nothing changes.
Unsupported means the claim is removed: its dimension abstains and M11 (never this code)
recomputes the verdict for the provisional version ``base + 1``. Only then is the new version
written to M2, in ONE idempotent call that carries the card (key = run id). A retry or a resumed
run therefore creates exactly one version, and no failure leaves a version without a card.
M2 answers 409 when ``base`` is not the latest version or another run already took ``base + 1``.
"""

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from common.errors import Conflict, NotFound, UpstreamError
from common.schemas import DimensionScore
from common.schemas.types import DIMENSIONS

from ..models.challenge_state import ChallengeState
from ..models.state import RunStatus
from . import builders
from .guard import guard
from .run_context import RunContext

Update = dict[str, Any]
Route = Callable[[ChallengeState], str]


# ---- pure routing ----
def after_load(state: ChallengeState) -> str:
    return "finish" if state.stopped or state.outcome else "check"  # outcome: already removed


def after_check(state: ChallengeState) -> str:
    if state.stopped or state.outcome != "removed":
        return "finish"
    return "recompute"


def after_recompute(state: ChallengeState) -> str:
    return "finish" if state.stopped else "save"


def after_save(state: ChallengeState) -> str:
    return "finish"


EDGES: dict[str, tuple[Route, list[str]]] = {
    "load": (after_load, ["check", "finish"]),
    "check": (after_check, ["recompute", "finish"]),
    "recompute": (after_recompute, ["save", "finish"]),
    "save": (after_save, ["finish"]),
}
STEP_NAMES = {
    "load": "LOAD",
    "check": "CHECK",
    "recompute": "RECOMPUTE",
    "save": "SAVE_VERSION",
    "finish": "FINISH",
}


def removal_request(
    verdict_input: dict[str, Any], dimension: str, *, version: int, run_id: str
) -> dict[str, Any]:
    """The recorded M11 input with one dimension abstained and the provisional version number."""
    scores = [DimensionScore.model_validate(s) for s in verdict_input["scores"]]
    if not any(s.dimension == dimension and s.score is not None for s in scores):
        raise Conflict(f"the claim's dimension {dimension} already abstains: nothing to remove")
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
            found = await profile.get_idea_version(state.user_id, state.idea_id, state.version)
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
        claims = card.get("claims") or []
        claim = next((c for c in claims if c.get("claim_id") == state.claim_id), None)
        if claim is None:
            raise NotFound(f"claim {state.claim_id} is not on this card")
        if claim.get("dimension") not in DIMENSIONS:  # fail closed: we cannot tell what to abstain
            raise Conflict(f"claim {state.claim_id} has no valid dimension")
        base = {"text": out.value["text"], "base_card": card, "claim": claim}
        if claim.get("removed"):  # challenged before: answer with what happened then
            entry: dict[str, Any] = next(
                (e for e in card.get("challenge_log") or [] if e.get("claim_id") == state.claim_id),
                {},
            )
            return {
                **base,
                "outcome": "removed",
                "reason": entry.get("reason") or "the claim was already removed",
                "already_removed": True,
                "new_version": state.version,  # the card that records the removal is this one
                "new_card": card,
                "saved": True,
            }
        return base

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

    async def recompute(self, state: ChallengeState) -> Update:
        base = state.base_card or {}
        claim = state.claim or {}
        if not isinstance(base.get("verdict_input"), dict):
            raise Conflict("the card has no recorded M11 inputs, so it cannot be recomputed")
        version = state.version + 1  # provisional: M2 confirms it when the card is saved
        request = removal_request(
            base["verdict_input"], str(claim["dimension"]), version=version, run_id=self.ctx.run_id
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
        if verdict.get("idea_id") != state.idea_id or verdict.get("version") != version:
            raise UpstreamError("M11 returned a verdict for a different idea or version")
        note = f"challenge removed {state.claim_id}"
        claims = [
            {**c, "removed": c.get("removed", False) or c.get("claim_id") == state.claim_id}
            for c in base.get("claims") or []
        ]
        card = {
            **verdict,
            "run_id": self.ctx.run_id,
            "claims": claims,
            "challenge_log": [
                *(base.get("challenge_log") or []),
                {"claim_id": state.claim_id, "outcome": "removed", "reason": state.reason},
            ],
            "cache_hit": False,
            "evidence": base.get("evidence") or {},
            "notes": [note],
            "degraded": False,
            "partial": False,
            "needs_input": [],
            "changes": [],
            "verdict_input": request,
            "change_note": note,
            "base_version": state.version,
        }
        return {"new_version": version, "new_card": card, "memo": out.memo_update}

    async def save(self, state: ChallengeState) -> Update:
        """One idempotent write of text, note and card (key = run id); 409 means we lost a race."""
        profile = self.ctx.clients.profile
        version = state.new_version or state.version + 1

        async def call() -> Any:
            try:
                created = await profile.put_idea_version(
                    user_id=state.user_id,
                    idea_id=state.idea_id,
                    version=version,
                    text=state.text or "",
                    change_note=f"challenge removed {state.claim_id}",
                    card=state.new_card or {},
                    idempotency_key=self.ctx.run_id,
                    base_version=state.version,
                )
            except Conflict as exc:
                raise Conflict(
                    f"{state.idea_id} has a newer version than v{state.version}, "
                    "or another change is already in progress"
                ) from exc
            return {"created": created}

        await self._step(
            state,
            "challenge_save",
            tool="m2.put_idea_version",
            inputs={"idea_id": state.idea_id, "version": version},
            call=call,
            timeout_s=self.t.profile,
            memoise=False,
        )
        return {"saved": True}

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
        if state.outcome == "removed" and state.saved:  # a card is shown only once it is saved
            result["new_version"] = state.new_version
            result["card"] = state.new_card
            if state.already_removed:
                result["already_removed"] = True
        if status is not RunStatus.SUCCEEDED:
            result["partial"] = True
            result["stop_reason"] = state.stop_reason
            result["error"] = state.error
        if status is RunStatus.SUCCEEDED:
            if state.saved and not state.already_removed and state.new_card is not None:
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
