"""Test doubles shared by the orchestrator's unit and component tests (not used at runtime)."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .models.challenge_state import ChallengeState
from .models.discover_state import DiscoverState
from .models.state import ValidateState


class FakeRedis:
    """The async Redis commands the orchestrator uses (hashes, pub/sub publish), in memory."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self.alive = True

    async def hincrby(self, name: str, key: str, amount: int) -> int:
        h = self.hashes.setdefault(name, {})
        h[key] = int(h.get(key, 0)) + amount
        return int(h[key])

    async def hincrbyfloat(self, name: str, key: str, amount: float) -> float:
        h = self.hashes.setdefault(name, {})
        h[key] = float(h.get(key, 0.0)) + amount
        return float(h[key])

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        return {k.encode(): str(v).encode() for k, v in self.hashes.get(name, {}).items()}

    async def expire(self, name: str, time: int) -> bool:
        self.ttls[name] = time
        return name in self.hashes

    async def ttl(self, name: str) -> int:
        return self.ttls.get(name, -1)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def ping(self) -> bool:
        if not self.alive:
            raise ConnectionError("redis is down")
        return True


# ---- a fake world of the other modules, served over httpx.MockTransport ----------------------
EVIDENCE_ITEMS: list[dict[str, Any]] = [
    {
        "evidence_id": f"card_{n:08x}#c0",
        "card_id": f"card_{n:08x}",
        "quote": f"people complain about slow exports ({n})",
        "source": source,
        "url": f"https://example.com/{n}",
        "retrieved_at": "2026-05-01T00:00:00Z",
        "score": 0.9,
        "rank": n,
        "card_name": f"Product {n}",
    }
    for n, source in ((1, "hackernews"), (2, "hackernews"), (3, "github"), (4, "reddit"))
]
PROFILE: dict[str, Any] = {
    "user_id": "u1",
    "skills": ["python"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "startup",
    "interests": [],
    "version": 3,
}
DIMS = ("demand_pain", "competition", "differentiation", "feasibility", "risk", "learning")
URLS = {
    "profile": "http://m2",
    "kb": "http://m6",
    "analyst": "http://m7",
    "scouts": "http://m4",
    "validators": "http://m9",
    "verdict": "http://m11",
    "critic": "http://m12",
    "ideation": "http://m8",
}
_HOSTS = {url.removeprefix("http://"): name for name, url in URLS.items()}


def fake_verdict(request: dict[str, Any], label: str = "GO", **over: Any) -> dict[str, Any]:
    """A schema-valid M11 response. The label is whatever the test says: it is not computed."""
    body: dict[str, Any] = {
        "idea_id": request["idea_id"],
        "version": request["version"],
        "run_id": request["run_id"],
        "verdict": label,
        "stability": "STABLE",
        "score": 72.5,
        "confidence": 0.8,
        "gates": {"evidence": "pass", "feasibility": "pass", "risk": "pass", "duplicate": "pass"},
        "weights": {
            "demand_pain": 25,
            "competition": 20,
            "differentiation": 20,
            "feasibility": 15,
            "risk": 15,
            "learning": 5,
        },
        "thresholds": {"T_go": 70, "T_no": 50, "floor": 1.5},
        "scores": request["scores"],
        "weakest_dimension": "competition",
        "what_would_make_go": [],
        "sensitivity": {"runs": 14, "flipped": 0},
        "versions": {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"},
    }
    body.update(over)
    return body


def voi_response(
    changes: bool,
    low: str = "GO",
    high: str = "NO-GO",
    low_value: float = 50,
    high_value: float = 200,
) -> dict[str, Any]:
    return {
        "low": {"value": low_value, "verdict": low, "stability": "STABLE", "score": 75.0},
        "high": {
            "value": high_value,
            "verdict": high if changes else low,
            "stability": "STABLE",
            "score": 40.0,
        },
        "verdict_changes": changes,
    }


_DEFAULT_LIMITS: dict[str, dict[str, Any]] = {
    "validate": {
        "max_tokens": 60000,
        "max_pages": 30,
        "max_steps": 80,
        "max_cost_usd": 0.05,
        "approval_threshold_usd": 0.05,
    },
    "whatif": {
        "max_tokens": 40000,
        "max_pages": 10,
        "max_steps": 60,
        "max_cost_usd": 0.03,
        "approval_threshold_usd": 0.03,
    },
    "challenge": {
        "max_tokens": 10000,
        "max_pages": 0,
        "max_steps": 20,
        "max_cost_usd": 0.01,
        "approval_threshold_usd": 0.01,
    },
    "discover": {
        "max_tokens": 250000,
        "max_pages": 60,
        "max_steps": 400,
        "max_cost_usd": 0.25,
        "approval_threshold_usd": 0.25,
    },
}


def make_idea(n: int, **over: Any) -> dict[str, Any]:
    """A schema-valid M8 idea. ``n`` makes title, ref and gap distinct."""
    idea: dict[str, Any] = {
        "idea_ref": f"idea_{n:010x}",
        "title": f"Idea number {n}",
        "summary": f"Summary {n}",
        "target_user": "students",
        "core_features": ["a", "b", "c"],
        "gap_ref": f"gap_{n}",
        "gap_evidence_ids": [f"card_{n:08x}#c0"],
        "fit_reason": "fits the profile",
        "est_scope": "M",
        "niche": "devtools",
        "complaints": [],
        "missing_features": [],
    }
    idea.update(over)
    return idea


def make_objection(n: int, idea: dict[str, Any], **over: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "objection_id": f"obj_{n}",
        "idea_ref": idea["idea_ref"],
        "dimension": "competition",
        "claim": f"competitor exists ({n})",
        "severity": "major",
        "evidence_ids": [f"card_{n:08x}#c0"],
    }
    obj.update(over)
    return obj


class FakeWorld:
    """Stands in for M2, M4, M6, M7, M9, M11 and M12. Tests tweak attributes before the run."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []  # (service, "METHOD /path", json body)
        self.evidence: list[dict[str, Any]] = list(EVIDENCE_ITEMS)
        self.profile: dict[str, Any] = dict(PROFILE)
        self.cache: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        # (service, "METHOD /path") -> queue of faults served before the real answer:
        # an int is an HTTP status, "timeout" raises a read timeout, "garbage" returns non-JSON
        self.faults: dict[tuple[str, str], list[int | str]] = {}
        self.hang: set[tuple[str, str]] = set()
        self.label = "GO"
        self.verdict_fn: Any = None
        self.voi_fn: Any = None
        self.score_fn: Any = None
        self.check_fn: Any = None
        self.features: dict[str, Any] = {"n_sources": 4, "trend_growth": 0.2}
        self.extra: dict[str, Any] = {
            "estimated_effort_hours": 100,
            "risk_blocker": None,
            "duplicate_of": None,
        }
        self.needs_approval = False
        self.scout_results: dict[str, Any] = {"fetched": 3, "raw_page_ids": ["p1", "p2", "p3"]}
        self.after_scout: list[dict[str, Any]] | None = None  # evidence once scouting happened
        self._scouted = False
        # M8 / M7 / M12 discover-side behaviour: tests replace these with their own functions
        self.ideate_fn: Any = None
        self.gap_fn: Any = None
        self.skeptic_fn: Any = None
        self.filter_fn: Any = None
        self.suggest_fn: Any = None
        self.rewrite_fn: Any = None
        self.niches_fn: Any = None
        self.gaps_fn: Any = None
        self.versions: dict[tuple[str, int], dict[str, Any]] = {}  # M2 idea versions

    # -- helpers -----------------------------------------------------------------------------
    def count(self, service: str, route: str) -> int:
        return sum(1 for s, r, _ in self.calls if (s, r) == (service, route))

    def bodies(self, service: str, route: str) -> list[Any]:
        return [b for s, r, b in self.calls if (s, r) == (service, route)]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        service = _HOSTS[request.url.host]
        route = f"{request.method} {request.url.path}"
        body = json.loads(request.content) if request.content else None
        self.calls.append((service, route, body))
        queue = self.faults.get((service, route))
        if queue:
            fault = queue.pop(0)
            if fault == "pass":
                pass  # one clean call, then the remaining faults apply
            elif fault == "timeout":
                raise httpx.ReadTimeout("fake timeout", request=request)
            elif fault == "garbage":
                return httpx.Response(200, content=b"<html>not json</html>")
            else:
                return httpx.Response(int(fault), json={"error": {"code": "UPSTREAM_ERROR"}})
        if (service, route) in self.hang:
            raise httpx.ReadTimeout("fake hang", request=request)
        status, payload = self._answer(route, request.url.path, body)
        return httpx.Response(status, json=payload)

    # -- the fake modules --------------------------------------------------------------------
    def _answer(self, route: str, path: str, body: Any) -> tuple[int, Any]:
        if route.startswith("GET /internal/profiles/"):
            return 200, self.profile
        if route.startswith("GET /internal/cache/verdict/"):
            key = path.removeprefix("/internal/cache/verdict/")
            return 200, {"hit": key in self.cache, "value": self.cache.get(key)}
        if route.startswith("PUT /internal/cache/verdict/"):
            self.cache[path.removeprefix("/internal/cache/verdict/")] = body["value"]
            return 200, {"ok": True}
        if route.startswith("PUT /internal/runs/"):
            self.runs[path.removeprefix("/internal/runs/")] = body
            return 200, {"ok": True}
        if route == "POST /search":
            items = self.after_scout if self._scouted and self.after_scout else self.evidence
            return 200, {"items": items, "index_version": 12, "cache_hit": False}
        if route == "POST /scout":
            self._scouted = True
            return 200, self.scout_results
        if route == "POST /features":
            return 200, self.features
        if route == "POST /score-all":
            if self.score_fn:
                return 200, self.score_fn(body)
            return 200, {"scores": self._scores(body), "extra": self.extra}
        if route == "GET /config":
            return 200, {
                "rules": {"rubric_versions": {d: f"{d}@v1" for d in DIMS}},
                "versions": {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"},
            }
        if route == "POST /verdict":
            if self.verdict_fn:
                return 200, self.verdict_fn(body)
            return 200, fake_verdict(body, self.label)
        if route == "POST /value-of-information":
            return 200, self.voi_fn(body) if self.voi_fn else voi_response(False)
        if route == "POST /check":
            if self.check_fn:
                return 200, self.check_fn(body)
            return 200, {
                "results": [
                    {"claim_id": c["claim_id"], "supported": True, "reason": "ok"}
                    for c in body["claims"]
                ]
            }
        if route == "POST /approval/evaluate":
            return 200, {"needs_approval": self.needs_approval, "spent": 0.04, "budget": 0.05}
        return self._discover_answer(route, path, body)

    def _discover_answer(self, route: str, path: str, body: Any) -> tuple[int, Any]:
        if route == "POST /niches/match":
            if self.niches_fn:
                return 200, self.niches_fn(body)
            return 200, {
                "matches": [
                    {
                        "niche_id": "n1",
                        "name": "devtools",
                        "status": "rising",
                        "size": 9,
                        "score": 1,
                    }
                ]
            }
        if route == "GET /gaps":
            if self.gaps_fn:
                return 200, self.gaps_fn()
            return 200, {
                "gaps": [
                    {
                        "id": "gap_1",
                        "niche_id": "n1",
                        "description": "slow exports",
                        "evidence_ids": ["card_00000001#c0"],
                        "complaint_count": 3,
                        "product_coverage": 0,
                        "score": 1.0,
                    }
                ]
            }
        if route == "POST /ideate":
            if self.ideate_fn:
                return 200, self.ideate_fn(body)
            ideas = [make_idea(n) for n in range(1, body["count"] + 1)]
            return 200, {"ideas": ideas, "dropped": [], "reason": None, "round": body["round"]}
        if route == "POST /gap-analyst":
            if self.gap_fn:
                return 200, self.gap_fn(body)
            ideas = [
                {**i, "complaints": [{"text": "slow", "evidence_ids": i["gap_evidence_ids"]}]}
                for i in body["ideas"]
            ]
            return 200, {"ideas": ideas, "discarded": 0}
        if route == "POST /skeptic":
            return 200, self.skeptic_fn(body) if self.skeptic_fn else {"objections": []}
        if route == "POST /objections/filter":
            if self.filter_fn:
                return 200, self.filter_fn(body)
            accepted = [
                {
                    "objection_id": o["objection_id"],
                    "dimension": o["dimension"],
                    "text": o["text"],
                    "evidence_ids": o["evidence_ids"],
                    "supporting_quote": "quote",
                }
                for o in body["objections"]
            ]
            return 200, {"accepted": accepted, "rejected": []}
        if route == "POST /suggest-changes":
            if self.suggest_fn:
                return 200, self.suggest_fn(body)
            change = {
                "change_id": f"chg_{body['target_dimension']}",
                "text": f"Improve {body['target_dimension']}",
                "target_dimension": body["target_dimension"],
                "rationale": "",
                "evidence_ids": ["card_00000001#c0"],
            }
            return 200, {"changes": [change]}
        if route == "POST /rewrite":
            if self.rewrite_fn:
                return 200, self.rewrite_fn(body)
            return 200, {
                "new_text": body["idea_text"] + " (revised: " + body["change_text"] + ")",
                "change_note": body["change_text"][:100],
            }
        if route.startswith("GET /internal/ideas/"):
            parts = path.strip("/").split("/")  # internal/ideas/{id}/versions/{v}
            found = self.versions.get((parts[2], int(parts[4])))
            return (200, found) if found else (404, {"error": {"code": "NOT_FOUND"}})
        if route.startswith("POST /internal/ideas/"):
            idea_id = path.strip("/").split("/")[2]
            latest = max((v for (i, v) in self.versions if i == idea_id), default=0)
            self.versions[(idea_id, latest + 1)] = {
                "idea_id": idea_id,
                "version": latest + 1,
                "text": body["text"],
                "card": {},
                "change_note": body["change_note"],
            }
            return 200, {"version": latest + 1}
        return 404, {"error": {"code": "NOT_FOUND"}}

    def _scores(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        ids = [e["evidence_id"] for e in body["evidence"]][:2]
        return [
            {
                "dimension": d,
                "score": 3.5 if ids else None,
                "confidence": 0.8 if ids else 0.0,
                "evidence_ids": ids,
                "reason": f"{d} looks fine",
                "rubric_version": f"{d}@v1",
                "model": "fake-m10",
            }
            for d in DIMS
        ]


class Rig:
    """Everything a run needs, wired to a ``FakeWorld`` with in-memory Redis, events and traces."""

    def __init__(
        self,
        world: FakeWorld | None = None,
        *,
        limits: dict[str, Any] | None = None,
        max_retries: int = 2,
        timeouts: dict[str, float] | None = None,
        deep_search_pages: int = 20,
        approval_timeout_s: float = 600,
        kind: str = "validate",
        debate_max_rounds: int = 3,
        children_concurrency: int = 3,
    ) -> None:
        from common.schemas import TraceRecord

        from .clients.factory import build_clients
        from .repositories.events import InMemoryPublisher
        from .services.run_config import run_config_from

        self.world = world or FakeWorld()
        self.redis = FakeRedis()
        self.publisher = InMemoryPublisher()
        self.records: list[TraceRecord] = []

        def section(of: str) -> dict[str, Any]:
            return {
                "max_retries": max_retries,
                "timeouts_s": {"scorer": 2, "kb_search": 1, "verdict": 1, **(timeouts or {})},
                **_DEFAULT_LIMITS[of],
                "debate": {"max_rounds": debate_max_rounds},
                "validate_children_concurrency": children_concurrency,
                "deep_search": {"max_pages": deep_search_pages, "est_cost_per_page_usd": 0.0008},
                **(limits or {}),
            }

        document = {
            **{k: section(k) for k in _DEFAULT_LIMITS},
            "concurrency": {"scorers": 4},
            "approval_timeout_s": approval_timeout_s,
        }
        self.configs = {k: run_config_from(document, k) for k in _DEFAULT_LIMITS}
        self.config = self.configs[kind]
        self.clients = build_clients(
            URLS,
            self.config,
            internal_token="test-token",  # noqa: S106
            backoff_s=0.0,
            transport=self.world.transport(),
        )

    def context(self, run_id: str, kind: str | None = None) -> Any:
        from common.budget import BudgetLedger
        from common.tracing import Tracer

        from .repositories.events import RunEmitter
        from .services.run_context import RunContext

        config = self.configs[kind] if kind else self.config
        return RunContext(
            run_id=run_id,
            config=config,
            ledger=BudgetLedger(self.redis, run_id, config.limits),
            tracer=Tracer("orchestrator", sink=self.records.append),
            emitter=RunEmitter(run_id, self.publisher),
            clients=self.clients,
        )


class Harness:
    """Runs the Validate graph directly (no run service) against a ``FakeWorld``."""

    def __init__(
        self,
        world: FakeWorld | None = None,
        *,
        run_id: str = "run_1",
        **rig_options: Any,
    ) -> None:
        from langgraph.checkpoint.memory import MemorySaver

        from .services.validate_graph import build_validate_graph, graph_config

        self.rig = Rig(world, **rig_options)
        self.world = self.rig.world
        self.run_id = run_id
        self.redis = self.rig.redis
        self.publisher = self.rig.publisher
        self.records = self.rig.records
        self.config = self.rig.config
        self.ctx = self.rig.context(run_id)
        self.ledger = self.ctx.ledger
        self.graph = build_validate_graph(self.ctx, MemorySaver())
        self._config = graph_config(run_id)

    def initial(self, **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "run_id": self.run_id,
            "user_id": "u1",
            "idea_id": "idea_1",
            "idea_version": 1,
            "idea_text": "A CLI that lints Dockerfiles for slow layers",
        }
        return {**base, **over}

    async def start(self, **over: Any) -> dict[str, Any]:
        state = ValidateState.model_validate(self.initial(**over))
        return dict(await self.graph.ainvoke(state, self._config))

    async def resume(self, value: Any) -> dict[str, Any]:
        from langgraph.types import Command

        return dict(await self.graph.ainvoke(Command(resume=value), self._config))

    async def state(self) -> ValidateState:
        snap = await self.graph.aget_state(self._config)
        return ValidateState.model_validate(snap.values)

    async def pending(self) -> dict[str, Any] | None:
        snap = await self.graph.aget_state(self._config)
        for task in snap.tasks:
            for itr in task.interrupts:
                return dict(itr.value)
        return None

    def event_types(self) -> list[str]:
        return [e.type for e in self.publisher.events_of(self.run_id)]

    def decisions(self) -> list[Any]:
        return [r for r in self.records if r.kind == "decision"]


ChildHook = Callable[
    [Callable[[ValidateState, str], Awaitable[ValidateState]], ValidateState, str],
    Awaitable[ValidateState],
]


class DiscoverHarness:
    """Runs the Discover graph directly against a ``FakeWorld`` (children included)."""

    def __init__(
        self,
        world: FakeWorld | None = None,
        *,
        run_id: str = "run_1",
        child_hook: ChildHook | None = None,
        **rig_options: Any,
    ) -> None:
        from langgraph.checkpoint.memory import MemorySaver

        from .services.discover_graph import build_discover_graph, make_child_runner
        from .services.validate_graph import graph_config

        self.rig = Rig(world, kind="discover", **rig_options)
        self.world = self.rig.world
        self.run_id = run_id
        self.redis = self.rig.redis
        self.publisher = self.rig.publisher
        self.records = self.rig.records
        self.ctx = self.rig.context(run_id)
        self.ledger = self.ctx.ledger
        saver = MemorySaver()
        base = make_child_runner(self.ctx, saver)

        async def run_child(state: ValidateState, thread: str) -> ValidateState:
            return (
                await child_hook(base, state, thread) if child_hook else await base(state, thread)
            )

        self.graph = build_discover_graph(self.ctx, saver, run_child)
        self._config = graph_config(run_id)

    async def start(self, **over: Any) -> DiscoverState:
        base: dict[str, Any] = {"run_id": self.run_id, "user_id": "u1", "count": 5}
        state = DiscoverState.model_validate({**base, **over})
        await self.graph.ainvoke(state, self._config)
        snap = await self.graph.aget_state(self._config)
        return DiscoverState.model_validate(snap.values)

    def event_types(self) -> list[str]:
        return [e.type for e in self.publisher.events_of(self.run_id)]

    def events(self, type_: str) -> list[Any]:
        return [e for e in self.publisher.events_of(self.run_id) if e.type == type_]


class ChallengeHarness:
    """Runs the Challenge graph directly against a ``FakeWorld``."""

    def __init__(
        self, world: FakeWorld | None = None, *, run_id: str = "run_1", **rig_options: Any
    ) -> None:
        from langgraph.checkpoint.memory import MemorySaver

        from .services.challenge import build_challenge_graph
        from .services.validate_graph import graph_config

        self.rig = Rig(world, kind="challenge", **rig_options)
        self.world = self.rig.world
        self.run_id = run_id
        self.publisher = self.rig.publisher
        self.records = self.rig.records
        self.ctx = self.rig.context(run_id)
        self.graph = build_challenge_graph(self.ctx, MemorySaver())
        self._config = graph_config(run_id)

    async def start(self, **over: Any) -> ChallengeState:
        base: dict[str, Any] = {
            "run_id": self.run_id,
            "user_id": "u1",
            "idea_id": "idea_1",
            "version": 1,
            "claim_id": "cl_risk",
        }
        state = ChallengeState.model_validate({**base, **over})
        await self.graph.ainvoke(state, self._config)
        snap = await self.graph.aget_state(self._config)
        return ChallengeState.model_validate(snap.values)

    def event_types(self) -> list[str]:
        return [e.type for e in self.publisher.events_of(self.run_id)]
