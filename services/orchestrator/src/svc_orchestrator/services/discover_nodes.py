"""Nodes of the Discover graph (docs/harness/06 §1, 07 §3).

Ideas come from M8, objections are kept only if M12 backs them with evidence, drops and the loop
are pure rules (``debate``), and survivors are validated by the existing Validate graph. Nothing
here computes or changes a verdict; the ranking only reads what M11 returned.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from common.errors import BudgetExceeded, DomainError
from common.logging import get_logger

from ..models.discover_state import DiscoverState
from ..models.ideas import Idea, Objection
from ..models.state import RunStatus, ValidateState
from . import debate
from .run_context import RunContext

log = get_logger("discover")

Update = dict[str, Any]
RunChild = Callable[[ValidateState, str], Awaitable[ValidateState]]

MAX_NICHES = 3
M8_MAX_GAPS = 20
GAP_TEXT_MAX = 1000
M8_NICHE_STATUS = {"rising", "flat", "saturated"}
EFFORT = "estimated_effort_hours"


def _ideas(raw: list[dict[str, Any]]) -> list[Idea]:
    return [Idea.model_validate(i) for i in raw]


def _dump(idea: Idea) -> dict[str, Any]:
    return idea.model_dump(mode="json")


class DiscoverNodes:
    def __init__(self, ctx: RunContext, run_child: RunChild) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self.t = ctx.config.timeouts
        self._run_child = run_child

    def _step(self, state: DiscoverState, step: str, **kw: Any) -> Awaitable[Any]:
        return self.ctx.runner.run(state.memo, step, **kw)

    # ---- REQUEST ----
    async def request(self, state: DiscoverState) -> Update:
        async def call() -> Any:
            return (await self.ctx.clients.profile.get_profile(state.user_id)).model_dump(
                mode="json"
            )

        out = await self._step(
            state,
            "discover_profile",
            tool="m2.get_profile",
            inputs={"user_id": state.user_id},
            call=call,
            timeout_s=self.t.profile,
            memoise=False,
        )
        return {"profile": out.value}

    # ---- NICHES ----
    async def niches(self, state: DiscoverState) -> Update:
        profile = state.profile or {}

        async def match() -> Any:
            found = await self.ctx.clients.analyst.match_niches(
                profile, state.niche, limit=MAX_NICHES
            )
            return [m.model_dump() for m in found]

        out = await self._step(
            state,
            "niches",
            tool="m7.match_niches",
            inputs={"niche": state.niche, "profile": state.profile},
            call=match,
            timeout_s=self.t.features,
        )
        matches: list[dict[str, Any]] = out.value[:MAX_NICHES]
        niches = [
            {"name": m["name"], "status": m["status"] if m["status"] in M8_NICHE_STATUS else None}
            for m in matches
        ]
        gaps: list[dict[str, Any]] = []
        notes: list[str] = []
        memo: dict[str, Any] = dict(out.memo_update)
        for m in matches:

            async def fetch(niche_id: str = m["niche_id"]) -> Any:
                return await self.ctx.clients.analyst.gaps(niche_id)

            try:
                found = await self._step(
                    state,
                    "gaps",
                    tool="m7.gaps",
                    inputs={"niche_id": m["niche_id"]},
                    call=fetch,
                    timeout_s=self.t.features,
                )
            except BudgetExceeded:
                raise
            except DomainError as exc:
                notes.append(f"gaps for niche {m['name']} unavailable ({exc.message})")
                continue
            memo.update(found.memo_update)
            for gap in found.value:
                if all(g["gap_id"] != gap["gap_id"] for g in gaps):
                    gaps.append({**gap, "niche": m["name"], "text": gap["text"][:GAP_TEXT_MAX]})
        if not gaps:
            notes.append("no gaps found for the matched niches: nothing to build ideas on")
        return {"niches": niches, "gaps": gaps[:M8_MAX_GAPS], "notes": notes, "memo": memo}

    # ---- IDEATE ----
    async def ideate(self, state: DiscoverState) -> Update:
        count = debate.ideation_count(state.round, state.count, len(state.candidates))

        async def call() -> Any:
            found = await self.ctx.clients.ideation.ideate(
                run_id=self.ctx.run_id,
                profile=state.profile or {},
                niches=state.niches,
                gaps=state.gaps,
                round_=state.round,
                previous_objections=state.previous_objections,
                count=count,
            )
            return {
                "ideas": [_dump(i) for i in found.ideas],
                "reason": found.reason,
                "skipped": found.skipped,
            }

        out = await self._step(
            state,
            "ideate",
            tool="m8.ideate",
            inputs={
                "round": state.round,
                "count": count,
                "niches": state.niches,
                "gaps": state.gaps,
                "previous": state.previous_objections,
            },
            call=call,
            timeout_s=self.t.llm,
        )
        fresh = {i["idea_ref"]: i for i in out.value["ideas"]}  # same title twice: last one wins
        notes: list[str] = []
        if not fresh:
            notes.append(
                f"round {state.round}: no ideas ({out.value['reason'] or 'none returned'})"
            )
        if out.value["skipped"]:
            notes.append(f"{out.value['skipped']} malformed ideas were dropped")
        by_ref = {c["idea_ref"]: c for c in state.candidates}
        by_ref.update(fresh)  # a revised idea replaces its earlier version
        return {
            "candidates": list(by_ref.values()),
            "pending": list(fresh.values()),
            "rounds_run": state.round,
            "notes": notes,
            "memo": out.memo_update,
        }

    # ---- GAP ANALYST ----
    async def gap_analyst(self, state: DiscoverState) -> Update:
        async def call() -> Any:
            found = await self.ctx.clients.ideation.gap_analyst(self.ctx.run_id, state.pending)
            return [_dump(i) for i in found]

        out = await self._step(
            state,
            "gap_analyst",
            tool="m8.gap_analyst",
            inputs={"ideas": state.pending},
            call=call,
            timeout_s=self.t.llm,
        )
        enriched = {i["idea_ref"]: i for i in out.value}
        # an idea the analyst did not return has no findings, so the rule below drops it
        merged = _ideas([enriched.get(p["idea_ref"], p) for p in state.pending])
        kept, dropped = debate.require_gap_evidence(merged, round_=state.round)
        await self._announce_drops(dropped)
        gone = {d["idea_ref"] for d in dropped}
        by_ref = {c["idea_ref"]: c for c in state.candidates if c["idea_ref"] not in gone}
        by_ref.update({i.idea_ref: _dump(i) for i in kept})
        return {
            "candidates": list(by_ref.values()),
            "pending": [_dump(i) for i in kept],
            "dropped": dropped,
            "memo": out.memo_update,
        }

    # ---- SKEPTIC ----
    async def skeptic(self, state: DiscoverState) -> Update:
        async def call() -> Any:
            found = await self.ctx.clients.ideation.skeptic(
                self.ctx.run_id, state.pending, profile=state.profile
            )
            return [o.model_dump(mode="json") for o in found]

        try:
            out = await self._step(
                state,
                "skeptic",
                tool="m8.skeptic",
                inputs={"ideas": state.pending},
                call=call,
                timeout_s=self.t.llm,
            )
        except BudgetExceeded:
            raise
        except DomainError as exc:  # no objections can be raised: say so, do not invent any
            return {
                "raised": [],
                "degraded": True,
                "notes": [
                    f"round {state.round}: skeptic failed ({exc.message}); ideas unchallenged"
                ],
            }
        refs = {p["idea_ref"] for p in state.pending}
        unique = {o["objection_id"]: o for o in out.value if o["idea_ref"] in refs}
        return {"raised": list(unique.values()), "memo": out.memo_update}

    # ---- CRITIC (objection filter + drop rules + loop decision) ----
    async def critic(self, state: DiscoverState) -> Update:
        raised = [Objection.model_validate(o) for o in state.raised]
        accepted: list[Objection] = []
        notes: list[str] = []
        memo: dict[str, Any] = {}
        degraded = False
        if raised:

            async def call() -> Any:
                kept = await self.ctx.clients.critic.filter_objections(
                    self.ctx.run_id, [o.model_dump() for o in raised]
                )
                return sorted(kept.accepted_ids)

            try:
                out = await self._step(
                    state,
                    "objection_filter",
                    tool="m12.objections_filter",
                    inputs={"objections": state.raised},
                    call=call,
                    timeout_s=self.t.critic,
                )
                accepted = [o for o in raised if o.objection_id in set(out.value)]
                memo = out.memo_update
            except BudgetExceeded:
                raise
            except DomainError as exc:  # unverified objections never defeat an idea
                degraded = True
                notes.append(f"round {state.round}: objection filter failed ({exc.message})")
        pending = _ideas(state.pending)
        outcome = debate.apply_objections(pending, accepted, round_=state.round)
        await self._announce_drops(outcome.dropped)
        gone = {d["idea_ref"] for d in outcome.dropped}
        candidates = [c for c in state.candidates if c["idea_ref"] not in gone]
        fresh = debate.unseen(outcome.nonfatal, set(state.seen_objections))
        kept_map = {k: v for k, v in state.accepted.items() if k not in gone}
        for idea in outcome.survivors:  # a revised idea starts again with this round's objections
            kept_map[idea.idea_ref] = [
                o.model_dump() for o in outcome.nonfatal if o.idea_ref == idea.idea_ref
            ]
        again = debate.should_continue(
            state.round,
            self.cfg.debate_max_rounds,
            new_nonfatal=len(fresh),
            survivors=len(candidates),
            requested=state.count,
        )
        self.ctx.runner.decision(
            "debate_round",
            {"round": state.round, "raised": len(raised), "accepted": len(accepted)},
            {
                "dropped": len(outcome.dropped),
                "new_nonfatal": len(fresh),
                "survivors": len(candidates),
                "continue": again,
            },
        )
        update: Update = {
            "candidates": candidates,
            "pending": [],
            "raised": [],
            "dropped": outcome.dropped,
            "accepted": kept_map,
            "seen_objections": [*state.seen_objections, *(o.objection_id for o in accepted)],
            "continue_debate": again,
            "notes": notes,
            "memo": memo,
        }
        if again:
            update["round"] = state.round + 1
            update["previous_objections"] = [o.model_dump() for o in fresh]
        if degraded:
            update["degraded"] = True
        return update

    async def _announce_drops(self, dropped: list[dict[str, Any]]) -> None:
        for d in dropped:
            await self.ctx.emitter.emit(
                "idea_dropped",
                title=d["title"],
                round=d["round"],
                reason=d["reason"],
                defeated_by_evidence_ids=d["defeated_by_evidence_ids"],
            )

    # ---- VALIDATE SURVIVORS ----
    async def validate_survivors(self, state: DiscoverState) -> Update:
        await self.ctx.ledger.check()  # no children at all when the run is already over budget
        ideas = _ideas(state.candidates)
        order = {i.idea_ref: n for n, i in enumerate(ideas)}
        chosen = sorted(
            ideas, key=lambda i: (len(state.accepted.get(i.idea_ref, [])), order[i.idea_ref])
        )
        chosen = chosen[: state.count]
        gate = asyncio.Semaphore(self.cfg.children_concurrency)

        async def one(idea: Idea) -> tuple[dict[str, Any], str | None]:
            child = ValidateState(
                run_id=self.ctx.run_id,
                user_id=state.user_id,
                idea_id=idea.idea_ref,
                idea_version=1,
                idea_text=debate.idea_text(idea),
                title=idea.title,
                overrides={EFFORT: float(debate.scope_hours(idea.est_scope))},
                interactive=False,
                objections=debate.m11_objections(
                    [Objection.model_validate(o) for o in state.accepted.get(idea.idea_ref, [])]
                ),
            )
            async with gate:
                try:
                    final = await self._run_child(child, f"{self.ctx.run_id}:{idea.idea_ref}")
                    card = final.card or {"verdict": None, "partial": True, "error": "no result"}
                    stop = final.stop_reason if final.status is RunStatus.BUDGET_STOPPED else None
                except Exception as exc:  # one broken child must not lose the others
                    log.error("child_crashed", idea=idea.idea_ref, error=type(exc).__name__)
                    card, stop = {"verdict": None, "partial": True, "error": "internal error"}, None
            item = debate.result_item(idea, card, 1)
            await self.ctx.emitter.emit(
                "partial_result",
                idea_id=item["idea_id"],
                verdict=item["verdict"],
                score=item["score"],
            )
            return item, stop

        done = await asyncio.gather(*(one(i) for i in chosen))
        update: Update = {"items": [item for item, _ in done]}
        if reason := next((stop for _, stop in done if stop), None):
            update["status"] = RunStatus.BUDGET_STOPPED
            update["stop_reason"] = reason
        return update

    # ---- RESULT ----
    async def result(self, state: DiscoverState) -> Update:
        status = state.status if state.stopped else RunStatus.SUCCEEDED
        card: dict[str, Any] = {
            "ideas": debate.rank(state.items),
            "dropped": [
                {
                    "idea_id": d["idea_ref"],
                    "title": d["title"],
                    "round": d["round"],
                    "reason": d["reason"],
                    "defeated_by_evidence_ids": d["defeated_by_evidence_ids"],
                }
                for d in state.dropped
            ],
            "rounds": state.rounds_run,
            "notes": list(state.notes),
            "degraded": state.degraded,
            "partial": state.stopped,
        }
        if state.stopped:
            card["stop_reason"] = state.stop_reason
            card["error"] = state.error
        if status is RunStatus.SUCCEEDED:
            await self.ctx.emitter.emit("run_completed", status="succeeded")
        elif status is RunStatus.BUDGET_STOPPED:
            await self.ctx.emitter.emit("budget_stopped", reason=state.stop_reason)
        else:
            await self.ctx.emitter.emit("run_failed", status="failed", reason=state.error)
        return {"card": card, "status": status}


__all__ = ["DiscoverNodes", "RunChild"]
