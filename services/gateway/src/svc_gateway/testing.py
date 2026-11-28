"""In-process stand-ins for M2, M3 and the Redis event bus, for the gateway's own tests.

They mimic the internal contracts (ownership checks included) so component tests exercise the real
gateway code over ``httpx.MockTransport``. Nothing here is imported by the running service.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from common.http import ServiceClient
from common.schemas import RunEvent

from .clients.agentops import AgentOpsClient
from .clients.events import Subscription
from .clients.ideas import IdeaClient
from .clients.orchestrator import OrchestratorClient
from .clients.profile import ProfileClient

INTERNAL_TOKEN = "internal-token"  # noqa: S105 (test value)
SUMMARY_KEYS = [
    "version",
    "text",
    "change_note",
    "parent_version",
    "created_at",
    "verdict",
    "score",
    "stability",
]
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _json(request: httpx.Request) -> dict[str, Any]:
    return dict(json.loads(request.content)) if request.content else {}


class FakeM2:
    """M2 internal API for ideas, versions, compare and runs. Reads are scoped by ``user_id``."""

    def __init__(self) -> None:
        self.ideas: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: int | None = None
        self.timeout = False
        self.ready = True
        self.fail_put_run = False

    # ---- seeding helpers -----------------------------------------------------------------
    def add_idea(
        self,
        user_id: str,
        title: str = "Study planner",
        text: str = "An app that plans study time",
        card: dict[str, Any] | None = None,
        verdict: str | None = None,
        score: float | None = None,
    ) -> str:
        idea_id = str(uuid.uuid4())
        self.ideas[idea_id] = {"user_id": user_id, "title": title, "source": "own"}
        self.versions[idea_id] = []
        self.add_version(idea_id, text, card, verdict, score)
        return idea_id

    def add_version(
        self,
        idea_id: str,
        text: str,
        card: dict[str, Any] | None = None,
        verdict: str | None = None,
        score: float | None = None,
    ) -> int:
        number = len(self.versions[idea_id]) + 1
        self.versions[idea_id].append(
            {
                "version": number,
                "text": text,
                "change_note": None,
                "parent_version": None,
                "created_at": NOW.isoformat(),
                "verdict": verdict,
                "score": score,
                "stability": "STABLE" if verdict else None,
                "clarifications": None,
                "verdict_payload": card,
            }
        )
        return number

    def add_run(
        self, run_id: str, user_id: str, kind: str = "validate", state: str = "running"
    ) -> None:
        self.runs[run_id] = {"user_id": user_id, "kind": kind, "state": state, "input": {}}

    # ---- transport -----------------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = _json(request)
        self.calls.append((request.method, request.url.path, body or dict(request.url.params)))
        if self.timeout:
            raise httpx.ReadTimeout("slow")
        if request.headers.get("x-internal-token") != INTERNAL_TOKEN:
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
        if self.fail:
            return httpx.Response(self.fail, json={"error": {"message": "secret internal detail"}})
        path = request.url.path
        if path == "/ready":
            return httpx.Response(200 if self.ready else 503, json={})
        parts = path.strip("/").split("/")  # internal, <area>, ...
        area = parts[1] if len(parts) > 1 else ""
        if area == "ideas":
            return self._ideas(request, parts[2:], body)
        if area == "compare":
            return self._compare(request)
        if area == "runs":
            return self._runs(request, parts[2:], body)
        return httpx.Response(404, json={})

    def _owned(self, idea_id: str, user_id: str | None) -> dict[str, Any] | None:
        idea = self.ideas.get(idea_id)
        return idea if idea is not None and idea["user_id"] == user_id else None

    def _ideas(
        self, request: httpx.Request, rest: list[str], body: dict[str, Any]
    ) -> httpx.Response:
        user = request.url.params.get("user_id")
        if not rest:
            if request.method == "POST":
                idea_id = self.add_idea(body["user_id"], body["title"], body["text"])
                return httpx.Response(201, json={"idea_id": idea_id, "version": 1})
            return self._page(request, user)
        idea_id = rest[0]
        if rest[1] == "versions" and request.method == "POST":
            if self._owned(idea_id, body["user_id"]) is None:
                return httpx.Response(404, json={})
            number = self.add_version(idea_id, body["text"])
            return httpx.Response(201, json={"idea_id": idea_id, "version": number})
        idea = self._owned(idea_id, user)
        if idea is None:
            return httpx.Response(404, json={})
        if len(rest) == 2:
            return httpx.Response(200, json=[self._summary(v) for v in self.versions[idea_id]])
        number = int(rest[2])
        found = [v for v in self.versions[idea_id] if v["version"] == number]
        if not found:
            return httpx.Response(404, json={})
        detail = {**self._summary(found[0]), "idea_id": idea_id, "title": idea["title"]}
        detail["clarifications"] = found[0]["clarifications"]
        detail["verdict_payload"] = found[0]["verdict_payload"]
        detail["internal_only"] = "never shown"
        return httpx.Response(200, json=detail)

    @staticmethod
    def _summary(version: dict[str, Any]) -> dict[str, Any]:
        return {k: version[k] for k in SUMMARY_KEYS}

    def _page(self, request: httpx.Request, user: str | None) -> httpx.Response:
        limit = int(request.url.params.get("limit", "20"))
        cursor = int(request.url.params.get("cursor") or 0)
        mine = [i for i, idea in self.ideas.items() if idea["user_id"] == user]
        chunk = mine[cursor : cursor + limit]
        items = []
        for idea_id in chunk:
            latest = self.versions[idea_id][-1]
            items.append(
                {
                    "idea_id": idea_id,
                    "title": self.ideas[idea_id]["title"],
                    "source": "own",
                    "latest_version": latest["version"],
                    "latest_verdict": latest["verdict"],
                    "score": latest["score"],
                    "updated_at": NOW.isoformat(),
                    "user_id": self.ideas[idea_id]["user_id"],
                }
            )
        more = cursor + limit < len(mine)
        return httpx.Response(
            200, json={"items": items, "next_cursor": str(cursor + limit) if more else None}
        )

    def _compare(self, request: httpx.Request) -> httpx.Response:
        user = request.url.params.get("user_id")
        rows = []
        for idea_id in request.url.params.get_list("idea_ids"):
            if self._owned(idea_id, user) is None:
                return httpx.Response(404, json={})
            latest = self.versions[idea_id][-1]
            rows.append(
                {
                    "idea_id": idea_id,
                    "title": self.ideas[idea_id]["title"],
                    "version": latest["version"],
                    "verdict": latest["verdict"],
                    "stability": latest["stability"],
                    "score": latest["score"],
                    "confidence": 0.8 if latest["verdict"] else None,
                    "scores": (latest["verdict_payload"] or {}).get("scores", {}),
                }
            )
        return httpx.Response(200, json=rows)

    def _runs(
        self, request: httpx.Request, rest: list[str], body: dict[str, Any]
    ) -> httpx.Response:
        run_id = rest[0]
        if request.method == "PUT":
            if self.fail_put_run:
                return httpx.Response(503, json={})
            existing = self.runs.get(run_id)
            if existing is not None and existing["user_id"] != body["user_id"]:
                return httpx.Response(404, json={})
            self.runs[run_id] = body
            return httpx.Response(200, json={"run_id": run_id, **body})
        run = self.runs.get(run_id)
        if run is None or run["user_id"] != request.url.params.get("user_id"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"run_id": run_id, **run})


class FakeM3:
    """M3 internal API: start, read, answer, approve and the stored events."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: int | None = None
        self.timeout = False
        self.ready = True
        self.replay_status = 200
        self.conflict = False
        self.counter = 0
        self.cancelled: list[str] = []
        self.user_headers: list[tuple[str, str | None]] = []

    def posts(self, suffix: str = "") -> list[tuple[str, str, dict[str, Any]]]:
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = _json(request)
        self.calls.append((request.method, request.url.path, body or dict(request.url.params)))
        self.user_headers.append((request.url.path, request.headers.get("x-user-id")))
        if self.timeout:
            raise httpx.ReadTimeout("slow")
        if request.headers.get("x-internal-token") != INTERNAL_TOKEN:
            return httpx.Response(401, json={})
        if self.fail:
            return httpx.Response(self.fail, json={"error": {"message": "secret internal detail"}})
        path = request.url.path
        if path == "/ready":
            return httpx.Response(200 if self.ready else 503, json={})
        if request.method == "POST" and path == "/runs":
            self.counter += 1
            run_id = f"run_{self.counter:012x}"
            self.runs[run_id] = {"kind": body["kind"], "state": "running", "input": body["input"]}
            return httpx.Response(202, json={"run_id": run_id})
        parts = path.strip("/").split("/")
        run = self.runs.get(parts[1]) if len(parts) > 1 else None
        if run is None:
            return httpx.Response(404, json={})
        if len(parts) == 2:
            return httpx.Response(200, json=self.view(parts[1], run))
        if parts[2] == "events":
            if self.replay_status != 200:
                return httpx.Response(self.replay_status, json={})
            after = int(request.url.params.get("after", "0"))
            wanted = [e for e in self.events.get(parts[1], []) if e["seq"] > after]
            return httpx.Response(200, json={"events": wanted})
        if parts[2] == "cancel":
            self.cancelled.append(parts[1])
            run["state"] = "failed"
            return httpx.Response(202, json={"run_id": parts[1], "state": "failed"})
        if self.conflict:
            return httpx.Response(409, json={"error": {"message": "internal conflict detail"}})
        return httpx.Response(202, json={"run_id": parts[1], "state": "running"})

    @staticmethod
    def view(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "kind": run["kind"],
            "state": run["state"],
            "progress": run.get("progress", {"step": "EVIDENCE"}),
            "spent": run.get("spent", {"cost_usd": 0.01, "tokens": 10, "pages": 1, "steps": 2}),
            "budget": run.get("budget", {"max_cost_usd": 1.0}),
            "pending_question": run.get("pending_question"),
            "pending_approval": run.get("pending_approval"),
            "result": run.get("result"),
            "error": run.get("error"),
            "internal_only": "never shown",
        }


class FakeM13:
    """M13 internal API: one run's trace (a GET /runs here would be a bug)."""

    def __init__(self) -> None:
        self.traces: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.fail: int | None = None
        self.timeout = False
        self.wrong_run = False
        self.endless_chunks: int | None = None  # answer with this many 64 KiB chunks, no length
        self.declared_length: int | None = None  # answer with a huge Content-Length header
        self.served_chunks = 0

    async def _chunks(self, count: int) -> AsyncIterator[bytes]:
        for _ in range(count):
            self.served_chunks += 1
            yield b" " * 65536

    def add_trace(self, run_id: str, **extra: Any) -> None:
        self.traces[run_id] = {
            "run_id": run_id,
            "totals": {"steps": 2, "cost_usd": 0.01},
            "by_agent": {},
            "truncated": False,
            "tree": [],
            **extra,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        if self.timeout:
            raise httpx.ReadTimeout("slow")
        if request.headers.get("x-internal-token") != INTERNAL_TOKEN:
            return httpx.Response(401, json={})
        if self.fail:
            return httpx.Response(self.fail, json={"error": {"message": "secret internal detail"}})
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "trace":
            body = self.traces.get(parts[1])
            if body is None:
                return httpx.Response(404, json={"error": {"message": "no trace for run"}})
            if self.endless_chunks is not None:
                return httpx.Response(200, content=self._chunks(self.endless_chunks))
            if self.declared_length is not None:
                return httpx.Response(
                    200,
                    headers={"content-length": str(self.declared_length)},
                    content=self._chunks(1),
                )
            if self.wrong_run:
                body = {**body, "run_id": "run_someoneelse"}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={})


def agentops_client(m13: FakeM13, max_bytes: int = 1024 * 1024) -> AgentOpsClient:
    transport = httpx.MockTransport(m13)
    http = ServiceClient(
        "http://agentops.internal.test:8000",
        timeout_s=1,
        backoff_s=0,
        internal_token=INTERNAL_TOKEN,
        transport=transport,
    )
    return AgentOpsClient(http, max_bytes)


def make_event(run_id: str, seq: int, type_: str = "agent_started", **payload: Any) -> RunEvent:
    return RunEvent(run_id=run_id, seq=seq, ts=NOW, type=type_, payload=payload)


class ScriptedSubscription:
    """Delivers a script: a string is a pub/sub message, ``None`` is an idle timeout."""

    def __init__(self, script: list[str | None], then_idle: bool = True) -> None:
        self._script = list(script)
        self._then_idle = then_idle
        self.closed = False
        self.timeouts: list[float] = []

    async def next(self, timeout_s: float) -> str | None:
        self.timeouts.append(timeout_s)
        if self._script:
            return self._script.pop(0)
        if self._then_idle:
            await asyncio.sleep(3600)
        return None

    async def aclose(self) -> None:
        self.closed = True


class ScriptedBus:
    def __init__(self, script: Sequence[str | None] | None = None, then_idle: bool = False) -> None:
        self.script: list[str | None] = list(script or [])
        self.then_idle = then_idle
        self.subscriptions: list[ScriptedSubscription] = []
        self.channels: list[str] = []

    async def subscribe(self, run_id: str) -> Subscription:
        self.channels.append(run_id)
        sub = ScriptedSubscription(self.script, self.then_idle)
        self.subscriptions.append(sub)
        return sub


def clients(m2: FakeM2, m3: FakeM3) -> tuple[IdeaClient, OrchestratorClient, ProfileClient]:
    def make(handler: Any, host: str) -> ServiceClient:
        return ServiceClient(
            f"http://{host}.internal.test:8000",
            timeout_s=1,
            backoff_s=0,
            internal_token=INTERNAL_TOKEN,
            transport=httpx.MockTransport(handler),
        )

    m2_client = make(m2, "profile")
    return (
        IdeaClient(m2_client),
        OrchestratorClient(make(m3, "orchestrator")),
        ProfileClient(m2_client),
    )
