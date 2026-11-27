"""Nodes of the Validate graph (docs/harness/07 §2).

Each node is ``async (state) -> partial update``. Nodes call other modules only through the typed
clients and only via ``StepRunner`` (budget, timeout, memo, trace). They never compute, adjust,
round or override a verdict: the verdict in the card is exactly what M11 returned.
"""

import asyncio
from collections.abc import Awaitable
from typing import Any, Protocol

from langgraph.types import interrupt
from pydantic import ValidationError

from common.budget import Spend
from common.errors import BudgetExceeded, DomainError, UpstreamError
from common.schemas import DimensionScore, EvidenceItem, Verdict
from svc_orchestrator.models.api import UnknownSpec

from ..models.state import RunStatus, ValidateState
from . import builders
from .cache_keys import verdict_cache_key
from .run_context import RunContext

Update = dict[str, Any]


class Node(Protocol):
    def __call__(self, state: ValidateState) -> Awaitable[Update]: ...


MAX_EVIDENCE = 30
SCOUT_SOURCES = ("hackernews", "github", "reddit")
SCOUT_ACTION = "deep_search"


def _dump_verdict(verdict: Verdict) -> dict[str, Any]:
    return verdict.model_dump(mode="json", by_alias=True)


def _scores(state: ValidateState) -> list[DimensionScore]:
    return [DimensionScore.model_validate(s) for s in state.scores]


class ValidateNodes:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self.t = ctx.config.timeouts

    # ---- guard: map failures to run status, announce the transition --------------------------
    def guarded(self, name: str, fn: Node, *, announce: bool = True) -> Node:
        async def node(state: ValidateState) -> Update:
            if announce:
                await self.ctx.emitter.emit("state_changed", **{"from": state.step, "to": name})
            try:
                update = await fn(state)
            except BudgetExceeded as exc:
                self.ctx.runner.decision(
                    "budget_stop", {"step": name}, {"reason": exc.message, "to": "card"}
                )
                update = {"status": RunStatus.BUDGET_STOPPED, "stop_reason": exc.message}
            except DomainError as exc:
                self.ctx.runner.decision(
                    "failed", {"step": name}, {"reason": exc.message, "to": "card"}
                )
                update = {"status": RunStatus.FAILED, "error": f"{name}: {exc.message}"}
            return {"step": name, **update}

        return node

    def _step(self, state: ValidateState, step: str, **kw: Any) -> Awaitable[Any]:
        return self.ctx.runner.run(state.memo, step, **kw)

    # ---- INTAKE ------------------------------------------------------------------------------
    async def intake(self, state: ValidateState) -> Update:
        c = self.ctx.clients

        async def profile() -> Any:
            return (await c.profile.get_profile(state.user_id)).model_dump(mode="json")

        async def versions() -> Any:
            return (await c.verdict.config()).model_dump()

        p = await self._step(
            state,
            "intake_profile",
            tool="m2.get_profile",
            inputs={"user_id": state.user_id},
            call=profile,
            timeout_s=self.t.profile,
            memoise=False,  # the profile can change between runs: always read it fresh
        )
        v = await self._step(
            state,
            "verdict_config",
            tool="m11.config",
            inputs={},
            call=versions,
            timeout_s=self.t.verdict,
            memoise=False,
        )
        cfg = v.value
        versions_map = {
            "engine": cfg["engine"],
            "rubrics": cfg["rubrics"],
            "profile_weights": cfg["weights"],
        }
        profile_version = p.value.get("version")
        if not isinstance(profile_version, int) or profile_version < 1:
            raise UpstreamError("M2 returned a profile without a version; refusing to guess one")
        key = verdict_cache_key(
            idea_id=state.idea_id,
            idea_version=state.idea_version,
            idea_text=state.idea_text,
            profile_version=profile_version,
            rubrics_version=cfg["rubrics"],
            weights_version=cfg["weights"],
            engine_version=cfg["engine"],
        )
        return {
            "profile": p.value,
            "versions": versions_map,
            "rubric_versions": cfg["rubric_versions"],
            "cache_key": key,
            "memo": {**p.memo_update, **v.memo_update},
        }

    # ---- CACHE_CHECK -------------------------------------------------------------------------
    async def cache_check(self, state: ValidateState) -> Update:
        if state.overrides or not state.cache_key:
            return {"notes": ["verdict cache skipped: user-supplied values change the result"]}
        key = state.cache_key
        try:
            out = await self._step(
                state,
                "cache_check",
                tool="m2.cache_get",
                inputs={"key": key},
                call=lambda: self._cache_get(key),
                timeout_s=self.t.cache,
                memoise=False,
            )
        except BudgetExceeded:
            raise
        except DomainError as exc:  # the cache is an optimisation: a miss is always safe
            return {"notes": [f"verdict cache unavailable: {exc.message}"]}
        card = out.value
        if not _valid_cached_card(card) or _key_mismatch(card, state) is not None:
            return {"memo": out.memo_update}
        self.ctx.runner.decision("cache_hit", {"key": key}, {"to": "card"})
        return {"cache_hit": True, "cached_card": card, "memo": out.memo_update}

    async def _cache_get(self, key: str) -> Any:
        return await self.ctx.clients.profile.get_cached_verdict(key) or {}

    # ---- EVIDENCE ----------------------------------------------------------------------------
    async def evidence(self, state: ValidateState) -> Update:
        pass_no = 2 if state.deep_search_done else 1
        queries = builders.evidence_queries(state.idea_text)

        async def search(query: str) -> Any:
            result = await self.ctx.clients.kb.search(query, run_id=self.ctx.run_id)
            return {
                "items": [i.model_dump(mode="json") for i in result.items],
                "skipped": result.skipped,
            }

        results = await asyncio.gather(
            *(
                self._step(
                    state,
                    "evidence",
                    tool="m6.search",
                    inputs={"query": q, "pass": pass_no},
                    call=lambda q=q: search(q),
                    timeout_s=self.t.kb_search,
                )
                for q in queries
            ),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BudgetExceeded):
                raise r
        failures = [r for r in results if isinstance(r, BaseException)]
        outcomes = [r for r in results if not isinstance(r, BaseException)]
        if not outcomes:
            raise failures[0]  # M6 is down: the run fails with a clear error
        notes: list[str] = []
        degraded = False
        if failures:
            notes.append(f"{len(failures)} of {len(queries)} evidence searches failed")
            degraded = True
        skipped = sum(int(o.value.get("skipped", 0)) for o in outcomes)
        if skipped:
            notes.append(f"{skipped} malformed evidence items were dropped")
        items = builders.merge_evidence(
            [EvidenceItem.model_validate(i) for i in o.value["items"]] for o in outcomes
        )[:MAX_EVIDENCE]
        n_sources, n_platforms = builders.evidence_stats(items)
        thin = builders.is_thin(n_sources, n_platforms)
        memo: dict[str, Any] = {}
        for o in outcomes:
            memo.update(o.memo_update)
        update: Update = {
            "evidence": [i.model_dump(mode="json") for i in items],
            "n_sources": n_sources,
            "n_platforms": n_platforms,
            "wants_deep_search": thin
            and self.cfg.deep_search_pages > 0
            and not state.deep_search_done,
            "notes": notes,
            "memo": memo,
        }
        if degraded:
            update["degraded"] = True
        self.ctx.runner.decision(
            "evidence_enough",
            {"n_sources": n_sources, "n_platforms": n_platforms},
            {
                "thin": thin,
                "to": "deep_search_decision" if update["wants_deep_search"] else "features",
            },
        )
        return update

    # ---- DEEP_SEARCH_DECISION / WAIT_APPROVAL / SCOUT ------------------------------------------
    async def deep_search_decision(self, state: ValidateState) -> Update:
        ledger = self.ctx.ledger
        pages, estimate = self.cfg.deep_search_pages, self.cfg.deep_search_cost_usd
        blocked = [
            f
            for f in await ledger.exceeded_fields(Spend(steps=1 + len(SCOUT_SOURCES), pages=pages))
            if f != "cost_usd"
        ]
        if blocked:  # steps/pages/tokens cannot be raised by an approval
            return {
                "deep_search_done": True,
                "wants_deep_search": False,
                "notes": [f"deep search skipped: it would exceed the {', '.join(blocked)} budget"],
            }
        spent, limits = await ledger.spent(), await ledger.limits()
        threshold = limits.approval_threshold_usd
        local_needs = threshold is not None and spent.cost_usd + estimate > threshold + 1e-9
        notes: list[str] = []

        async def evaluate() -> Any:
            return (
                await self.ctx.clients.critic.evaluate_approval(
                    self.ctx.run_id, SCOUT_ACTION, estimate
                )
            ).model_dump()

        try:
            out = await self._step(
                state,
                "approval_evaluate",
                tool="m12.approval_evaluate",
                inputs={"action": SCOUT_ACTION, "estimate": estimate, "spent": spent.cost_usd},
                call=evaluate,
                timeout_s=self.t.approval,
            )
            needs = bool(out.value["needs_approval"]) or local_needs
            memo = out.memo_update
        except BudgetExceeded:
            raise
        except DomainError as exc:  # spending money needs consent: ask when the guard is down
            needs, memo = True, {}
            notes.append(f"approval check unavailable ({exc.message}); asking the user")
        self.ctx.runner.decision(
            "deep_search_needs_approval",
            {"estimated_cost_usd": estimate, "spent_usd": spent.cost_usd},
            {"needs_approval": needs, "to": "await_approval" if needs else "scout"},
        )
        if not needs:
            return {"notes": notes, "memo": memo}
        approval = {
            "approval_id": f"ap_{self.ctx.run_id}_deep_search",
            "action": SCOUT_ACTION,
            "estimated_cost_usd": estimate,
            "spent_usd": spent.cost_usd,
            "budget_usd": limits.max_cost_usd,
            "description": f"Scout up to {pages} more pages to find more evidence",
        }
        await self.ctx.emitter.emit("approval_required", **approval)
        return {"pending_approval": approval, "notes": notes, "memo": memo}

    async def await_approval(self, state: ValidateState) -> Update:
        approval = state.pending_approval or {}
        decision = interrupt({"type": "approval", **approval})
        approved = decision == "approve"
        self.ctx.runner.decision(
            "approval_outcome",
            {"approval_id": approval.get("approval_id")},
            {"approved": approved, "to": "scout" if approved else "features"},
        )
        if approved:
            await self.ctx.ledger.raise_cost_limit(float(approval.get("estimated_cost_usd", 0)))
            return {"approval_decision": "approved", "pending_approval": None}
        return {
            "approval_decision": "declined",
            "pending_approval": None,
            "deep_search_done": True,
            "wants_deep_search": False,
            "notes": ["deep search declined: continuing with the evidence already collected"],
        }

    async def scout(self, state: ValidateState) -> Update:
        per_source = max(1, self.cfg.deep_search_pages // len(SCOUT_SOURCES))
        query = " ".join(state.idea_text.split())[:200]

        async def one(source: str) -> Any:
            return await self.ctx.clients.scouts.scout(source, max_pages=per_source, query=query)

        results = await asyncio.gather(
            *(
                self._step(
                    state,
                    "scout",
                    tool="m4.scout",
                    inputs={"source": s, "query": query, "max_pages": per_source},
                    call=lambda s=s: one(s),
                    timeout_s=self.t.scout,
                    estimate=Spend(steps=1, pages=per_source),
                )
                for s in SCOUT_SOURCES
            ),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BudgetExceeded):
                raise r
        failed = [r for r in results if isinstance(r, BaseException)]
        memo: dict[str, Any] = {}
        for r in results:
            if not isinstance(r, BaseException):
                memo.update(r.memo_update)
        update: Update = {
            "deep_search_done": True,
            "wants_deep_search": False,
            "memo": memo,
            "notes": [
                f"deep search scouted {len(results) - len(failed)} of {len(results)} sources"
            ],
        }
        if failed:
            update["degraded"] = True
        return update

    # ---- FEATURES ----------------------------------------------------------------------------
    async def features(self, state: ValidateState) -> Update:
        ids = [e["evidence_id"] for e in state.evidence]

        async def call() -> Any:
            profile = builders.profile_for_verdict(state.profile or {})
            return await self.ctx.clients.analyst.features(state.idea_text, ids, profile)

        try:
            out = await self._step(
                state,
                "features",
                tool="m7.features",
                inputs={"idea": state.idea_text, "evidence_ids": ids, "profile": state.profile},
                call=call,
                timeout_s=self.t.features,
            )
        except BudgetExceeded:
            raise
        except DomainError as exc:  # scorers can still run (and abstain) without features
            return {
                "features": {},
                "degraded": True,
                "notes": [f"features unavailable ({exc.message}); scoring without them"],
            }
        return {"features": out.value, "memo": out.memo_update}

    # ---- SCORERS -----------------------------------------------------------------------------
    async def scorers(self, state: ValidateState) -> Update:
        items = [EvidenceItem.model_validate(e) for e in state.evidence]
        allowed = {i.evidence_id for i in items}

        async def call() -> Any:
            result = await self.ctx.clients.validators.score_all(
                run_id=self.ctx.run_id,
                idea=state.idea_text,
                profile=state.profile or {},
                evidence=builders.scorer_evidence(items),
                features=state.features,
            )
            return {"scores": result.scores, "extra": result.extra}

        try:
            out = await self._step(
                state,
                "scorers",
                tool="m9.score_all",
                inputs={
                    "idea": state.idea_text,
                    "evidence_ids": sorted(allowed),
                    "features": state.features,
                    "profile": state.profile,
                    "rubrics": state.rubric_versions,
                },
                call=call,
                timeout_s=self.t.scorer,
            )
        except BudgetExceeded:
            raise
        except DomainError as exc:  # after retries (and M10's own fallback): abstain, never invent
            scores = builders.abstain_all(
                state.rubric_versions, f"scorers unavailable: {exc.message}"
            )
            return {
                "scores": [s.model_dump(mode="json") for s in scores],
                "degraded": True,
                "notes": [
                    f"scorers failed after retries ({exc.message}); every dimension abstained"
                ],
            }
        scores = builders.parse_scores(out.value["scores"], allowed, state.rubric_versions)
        extra = out.value["extra"]
        notes: list[str] = []
        abstained = [s.dimension for s in scores if s.score is None]
        if abstained:
            notes.append(f"abstained: {', '.join(abstained)}")
        update: Update = {
            "scores": [s.model_dump(mode="json") for s in scores],
            "scorer_extra": extra,
            "memo": out.memo_update,
            "notes": notes,
        }
        return update

    # ---- CLARIFY -----------------------------------------------------------------------------
    def _verdict_request(
        self, state: ValidateState, scores: list[DimensionScore]
    ) -> dict[str, Any]:
        return builders.build_verdict_request(
            idea_id=state.idea_id,
            version=state.idea_version,
            run_id=self.ctx.run_id,
            profile=state.profile or {},
            scores=scores,
            n_sources=state.n_sources,
            n_platforms=state.n_platforms,
            extra=state.scorer_extra,
            overrides=state.overrides,
            answers=state.answers,
            objections=[],
        )

    async def clarify_check(self, state: ValidateState) -> Update:
        if builders.effort_is_unknown(state.scorer_extra, state.overrides, state.answers):
            return await self._ask_for_effort(state)
        specs = builders.pending_unknowns(
            state.scorer_extra, state.overrides, state.unknowns, state.asked, state.vetted
        )
        scores = _scores(state)
        vetted: list[str] = []
        notes: list[str] = []
        memo: dict[str, Any] = {}
        for spec in specs:
            request = builders.voi_request(self._verdict_request(state, scores), spec)

            async def call(request: dict[str, Any] = request) -> Any:
                return (await self.ctx.clients.verdict.voi(request)).model_dump()

            try:
                out = await self._step(
                    state,
                    "value_of_information",
                    tool="m11.value_of_information",
                    inputs=request,
                    call=call,
                    timeout_s=self.t.verdict,
                )
            except BudgetExceeded:
                raise
            except DomainError as exc:  # no VOI means no question; /verdict decides if M11 is down
                vetted.append(spec.field)
                notes.append(f"could not check whether {spec.field} matters ({exc.message})")
                continue
            memo.update(out.memo_update)
            if out.value["verdict_changes"]:
                question = builders.question_payload(
                    spec,
                    low=(out.value["low"]["verdict"], out.value["low"]["value"]),
                    high=(out.value["high"]["verdict"], out.value["high"]["value"]),
                )
                self.ctx.runner.decision(
                    "ask_question",
                    {"field": spec.field},
                    {"asked": True, "why": question["why_it_matters"], "to": "await_answer"},
                )
                await self.ctx.emitter.emit(
                    "question",
                    question_id=question["question_id"],
                    field=question["field"],
                    required=question["required"],
                    range=question["range"],
                    text=question["text"],
                    why_it_matters=question["why_it_matters"],
                )
                return {
                    "pending_question": question,
                    "asked": [spec.field],
                    "vetted": vetted,
                    "notes": notes,
                    "memo": memo,
                }
            vetted.append(spec.field)
            self.ctx.runner.decision(
                "skip_question", {"field": spec.field}, {"asked": False, "why": "verdict unchanged"}
            )
        return {"pending_question": None, "vetted": vetted, "notes": notes, "memo": memo}

    async def _ask_for_effort(self, state: ValidateState) -> Update:
        """M11 requires the build hours. Ask once (within the cap); never send a made-up value."""
        field = "estimated_effort_hours"
        if field in state.asked or len(state.asked) >= builders.MAX_QUESTIONS:
            self.ctx.runner.decision(
                "missing_input", {"field": field}, {"verdict": None, "to": "card"}
            )
            return {
                "missing_inputs": [field],
                "notes": [f"no {field}: finished without a verdict instead of guessing one"],
            }
        question = builders.effort_question()
        self.ctx.runner.decision(
            "ask_question",
            {"field": field},
            {"asked": True, "why": question["why_it_matters"], "to": "await_answer"},
        )
        await self.ctx.emitter.emit(
            "question",
            question_id=question["question_id"],
            field=question["field"],
            required=question["required"],
            range=question["range"],
            text=question["text"],
            why_it_matters=question["why_it_matters"],
        )
        return {"pending_question": question, "asked": [field]}

    async def await_answer(self, state: ValidateState) -> Update:
        question = state.pending_question or {}
        answer = interrupt({"type": "question", **question})
        field = str(question.get("field"))
        if isinstance(answer, int | float) and not isinstance(answer, bool):
            return {"answers": {field: float(answer)}, "pending_question": None}
        if question.get("required"):
            note = f"no answer for {field}: it is required, so no verdict can be given"
        else:
            note = f"no answer for {field}: used the scorer's estimate instead (result not cached)"
        return {
            "answers": {field: None},  # NO_ANSWER (a timeout) or anything that is not a number
            "pending_question": None,
            "degraded": True,
            "notes": [note],
        }

    # ---- VERDICT (M11 only) --------------------------------------------------------------------
    async def verdict(self, state: ValidateState) -> Update:
        return await self._verdict(state, "verdict", recomputed=False)

    async def recompute(self, state: ValidateState) -> Update:
        update = await self._verdict(state, "verdict_recompute", recomputed=True)
        update["notes"] = ["unsupported claims were removed and M11 recomputed the verdict"]
        return update

    async def _verdict(self, state: ValidateState, step: str, *, recomputed: bool) -> Update:
        request = self._verdict_request(state, _scores(state))

        async def call() -> Any:
            return _dump_verdict(await self.ctx.clients.verdict.verdict(request))

        out = await self._step(
            state, step, tool="m11.verdict", inputs=request, call=call, timeout_s=self.t.verdict
        )
        if out.value.get("idea_id") != state.idea_id or out.value.get("version") != (
            state.idea_version
        ):
            raise UpstreamError("M11 returned a verdict for a different idea or version")
        return {
            "verdict": out.value,
            "recomputed": recomputed,
            "verdict_final": recomputed,  # a fresh verdict after removed claims is settled
            "memo": out.memo_update,
        }

    # ---- CHALLENGE (claim check) -----------------------------------------------------------------
    async def claims_check(self, state: ValidateState) -> Update:
        scores = _scores(state)
        claims = builders.build_claims(scores)
        if not claims:
            return {"claim_results": [], "verdict_final": True}  # nothing to challenge

        async def call() -> Any:
            response = await self.ctx.clients.critic.check(self.ctx.run_id, claims)
            return [r.model_dump(mode="json") for r in response.results]

        out = await self._step(
            state,
            "claims_check",
            tool="m12.check",
            inputs={"claims": claims},
            call=call,
            timeout_s=self.t.critic,
        )
        new_scores, removed = builders.apply_claim_results(scores, out.value)
        update: Update = {
            "claim_results": out.value,
            "removed_claims": removed,
            "verdict_final": not removed,  # claims were removed: M11 must recompute first
            "memo": out.memo_update,
        }
        if removed:
            update["scores"] = [s.model_dump(mode="json") for s in new_scores]
            update["notes"] = [f"claims removed as unsupported: {', '.join(removed)}"]
        return update

    # ---- STABILITY -----------------------------------------------------------------------------
    async def stability(self, state: ValidateState) -> Update:
        verdict = state.verdict or {}
        self.ctx.runner.decision(
            "stability",
            {"verdict": verdict.get("verdict")},
            {
                "stability": verdict.get("stability"),
                "sensitivity": verdict.get("sensitivity"),
                "what_would_make_go": len(verdict.get("what_would_make_go") or []),
            },
        )
        return {}

    # ---- CARD ----------------------------------------------------------------------------------
    async def card(self, state: ValidateState) -> Update:
        if state.cache_hit and state.cached_card:
            hit = {**state.cached_card, "cache_hit": True, "run_id": self.ctx.run_id}
            return await self._finish(state, hit, RunStatus.SUCCEEDED)
        status = state.status if state.stopped else RunStatus.SUCCEEDED
        cited = {e for s in state.scores for e in s.get("evidence_ids", [])}
        evidence = {e["evidence_id"]: e for e in state.evidence if e["evidence_id"] in cited}
        removed = set(state.removed_claims)
        settled = state.verdict is not None and state.verdict_final and not state.stopped
        card: dict[str, Any] = {
            **(state.verdict if settled and state.verdict else {"verdict": None}),
            "run_id": self.ctx.run_id,
            "claims": [{**r, "removed": r["claim_id"] in removed} for r in state.claim_results],
            "challenge_log": [
                {"claim_id": r["claim_id"], "outcome": "removed", "reason": r["reason"]}
                for r in state.claim_results
                if r["claim_id"] in removed
            ],
            "cache_hit": False,
            "evidence": evidence,
            "notes": list(state.notes),
            "degraded": state.degraded,
            "partial": not settled,
            "needs_input": list(state.missing_inputs),
        }
        if state.missing_inputs:
            card["missing"] = list(state.missing_inputs)
        if state.stopped:
            card["stop_reason"] = state.stop_reason
            card["error"] = state.error
            card["scores"] = state.scores
        extra: Update = {}
        if status is RunStatus.SUCCEEDED and not state.degraded and not state.overrides:
            if (reason := _key_mismatch(card, state)) is not None and settled:
                note = f"verdict not cached: {reason}"
                card["notes"].append(note)
                extra["notes"] = [note]
            else:
                await self._store_in_cache(state, card)
        return {**await self._finish(state, card, status), **extra}

    async def _store_in_cache(self, state: ValidateState, card: dict[str, Any]) -> None:
        answered = any(v is not None for v in state.answers.values())
        if not state.cache_key or answered or card["partial"]:
            return
        key = state.cache_key

        async def put() -> Any:
            await self.ctx.clients.profile.put_cached_verdict(key, card, self.ctx.verdict_ttl_s)
            return {"stored": True}

        try:
            await self._step(
                state,
                "cache_store",
                tool="m2.cache_put",
                inputs={"key": key},
                call=put,
                timeout_s=self.t.cache,
            )
        except (BudgetExceeded, DomainError):  # losing a cache write loses nothing but speed
            return

    async def _finish(
        self, state: ValidateState, card: dict[str, Any], status: RunStatus
    ) -> Update:
        if card.get("verdict") is not None:
            await self.ctx.emitter.emit("verdict", card=card)
        if status is RunStatus.SUCCEEDED:
            await self.ctx.emitter.emit("run_completed", status="succeeded")
        elif status is RunStatus.BUDGET_STOPPED:
            await self.ctx.emitter.emit("budget_stopped", reason=state.stop_reason)
        else:
            await self.ctx.emitter.emit("run_failed", status="failed", reason=state.error)
        return {"card": card, "status": status}


def _valid_cached_card(card: Any) -> bool:
    """A cache entry must be a complete card whose verdict parses; anything else is a miss."""
    if not isinstance(card, dict) or not card:
        return False
    if card.get("partial") or card.get("degraded"):
        return False
    try:
        Verdict.model_validate({k: v for k, v in card.items() if k in Verdict.model_fields})
    except ValidationError:
        return False
    return True


def _key_mismatch(card: dict[str, Any], state: ValidateState) -> str | None:
    """Why a card may not be stored under / served for this run's cache key, or ``None``."""
    if card.get("idea_id") != state.idea_id:
        return "idea_id differs from the cache key"
    if card.get("version") != state.idea_version:
        return "idea version differs from the cache key"
    if card.get("versions") != state.versions:
        return "engine/rubric/weights versions differ from the cache key"
    return None


__all__ = ["Node", "UnknownSpec", "ValidateNodes"]
