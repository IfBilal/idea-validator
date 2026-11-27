"""Test doubles shared by the orchestrator's unit and component tests (not used at runtime)."""

import json
from typing import Any

import httpx

from common.testing import EmulatedRedis

from .models.state import ValidateState


class FakeRedis(EmulatedRedis):
    """The async Redis the orchestrator uses: hashes, the budget reserve script, pub/sub publish."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, str]] = []
        self.alive = True

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
    ) -> None:
        from common.schemas import TraceRecord

        from .clients.factory import build_clients
        from .repositories.events import InMemoryPublisher
        from .services.run_config import run_config_from

        self.world = world or FakeWorld()
        self.redis = FakeRedis()
        self.publisher = InMemoryPublisher()
        self.records: list[TraceRecord] = []
        section: dict[str, Any] = {
            "max_retries": max_retries,
            "timeouts_s": {"scorer": 2, "kb_search": 1, "verdict": 1, **(timeouts or {})},
            "max_tokens": 60000,
            "max_pages": 30,
            "max_steps": 80,
            "max_cost_usd": 0.05,
            "approval_threshold_usd": 0.05,
            "deep_search": {"max_pages": deep_search_pages, "est_cost_per_page_usd": 0.0008},
            **(limits or {}),
        }
        self.config = run_config_from(
            {
                "validate": section,
                "concurrency": {"scorers": 4},
                "approval_timeout_s": approval_timeout_s,
            },
            "validate",
        )
        self.clients = build_clients(
            URLS,
            self.config,
            internal_token="test-token",  # noqa: S106
            backoff_s=0.0,
            transport=self.world.transport(),
        )

    def context(self, run_id: str) -> Any:
        from common.budget import BudgetLedger
        from common.tracing import Tracer

        from .repositories.events import RunEmitter
        from .services.run_context import RunContext

        return RunContext(
            run_id=run_id,
            config=self.config,
            ledger=BudgetLedger(self.redis, run_id, self.config.limits),
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
