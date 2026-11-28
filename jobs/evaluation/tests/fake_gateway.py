"""A scripted gateway (+ M13 trace endpoint) behind ``httpx.MockTransport`` for the runner tests."""

import json
from typing import Any

import httpx

DIMS = ("demand_pain", "competition", "differentiation", "feasibility", "risk", "learning")


def good_card(verdict: str | None = "GO", **over: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "verdict": verdict,
        "stability": "STABLE",
        "score": 71.5,
        "confidence": 0.8,
        "scores": [
            {"dimension": d, "score": 3.5, "evidence_ids": ["c1#a"], "reason": "grounded"}
            for d in DIMS
        ],
        "claims": [{"claim_id": "cl_1", "text": "people ask for it", "evidence_ids": ["c1#a"]}],
        "evidence": {"c1#a": {"text": "a thread"}},
        "reasons": ["clear demand"],
        "cache_hit": False,
    }
    card.update(over)
    return card


def good_trace() -> list[dict[str, Any]]:
    return [
        {
            "step_id": 1,
            "agent": "orchestrator",
            "kind": "tool_call",
            "tool": "m6.search",
            "latency_ms": 20,
            "cost_usd": 0.0,
        },
        {
            "step_id": 2,
            "agent": "validators",
            "kind": "tool_call",
            "tool": "m9.score_all",
            "latency_ms": 400,
            "cost_usd": 0.01,
        },
        {
            "step_id": 3,
            "agent": "orchestrator",
            "kind": "tool_call",
            "tool": "m11.verdict",
            "latency_ms": 5,
            "cost_usd": 0.0,
        },
    ]


class FakeGateway:
    """Scenario per item title (``eval <id>``). Unlisted items succeed with a GO card."""

    def __init__(self, scenarios: dict[str, dict[str, Any]] | None = None) -> None:
        self.scenarios = scenarios or {}
        self.users: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.answers: list[dict[str, Any]] = []
        self.approvals: list[dict[str, Any]] = []
        self.validate_attempts: dict[str, int] = {}
        self.failures_left: dict[str, list[int]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.polls: dict[str, int] = {}
        self.idem_keys: list[str] = []
        self.login_status = 200

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _json(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        body: Any = json.loads(request.content) if request.content else None
        if path == "/auth/login":
            if self.login_status != 200:
                return self._json(self.login_status, {})
            if body["email"] not in self.users:
                return self._json(401, {})
            return self._json(
                200, {"access_token": "tok", "token_type": "bearer", "expires_in": 60}
            )
        if path == "/auth/register":
            self.users.add(body["email"])
            return self._json(201, {"user_id": "u"})
        if path == "/profile":
            return self._json(200, body)
        if path == "/validate":
            return self._validate(request, body)
        if path.endswith("/trace"):
            return self._trace(path.split("/")[2])
        if path.endswith("/answer"):
            self.answers.append(body)
            self.runs[path.split("/")[2]]["answered"] = True
            return self._json(202, {"run_id": "r", "state": "running"})
        if path.endswith("/approve"):
            self.approvals.append(body)
            self.runs[path.split("/")[2]]["approved"] = True
            return self._json(202, {"run_id": "r", "state": "running"})
        if path.startswith("/runs/"):
            return self._run(path.split("/")[2])
        return self._json(404, {})

    def _validate(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        title = body["title"]
        self.idem_keys.append(request.headers.get("idempotency-key", ""))
        spec = self.scenarios.get(title, {})
        left = self.failures_left.setdefault(title, list(spec.get("validate_failures", [])))
        self.validate_attempts[title] = self.validate_attempts.get(title, 0) + 1
        if left:
            status = left.pop(0)
            return self._json(status, {}, {"retry-after": "0"} if status == 429 else None)
        run_id = f"run_{title.split()[-1]}_{self.validate_attempts[title]}"
        self.runs[run_id] = {"spec": spec}
        return self._json(202, {"run_id": run_id, "idea_id": "i", "version": 1})

    def _run(self, run_id: str) -> httpx.Response:
        run = self.runs[run_id]
        spec = run["spec"]
        n = self.polls[run_id] = self.polls.get(run_id, 0) + 1
        spent = spec.get("spent", {"cost_usd": 0.01, "tokens": 100, "pages": 1, "steps": 5})
        budget = {"max_cost_usd": 0.05, "max_tokens": 60000, "max_pages": 30, "max_steps": 80}
        view: dict[str, Any] = {
            "run_id": run_id,
            "kind": "validate",
            "state": "running",
            "progress": {},
            "spent": spent,
            "budget": budget,
            "pending_question": None,
            "pending_approval": None,
            "result": None,
            "error": None,
        }
        if spec.get("never_finishes"):
            return self._json(200, view)
        if spec.get("question") and not run.get("answered") and n == 1:
            view.update(state="awaiting_input", pending_question=spec["question"])
            return self._json(200, view)
        if spec.get("question") and not run.get("answered") and spec.get("question_blocks"):
            view.update(state="awaiting_input", pending_question=spec["question"])
            return self._json(200, view)
        if spec.get("approval") and not run.get("approved"):
            view.update(state="awaiting_approval", pending_approval=spec["approval"])
            return self._json(200, view)
        if n <= spec.get("polls", 1):
            return self._json(200, view)
        state = spec.get("state", "succeeded")
        result = spec["card"] if "card" in spec else good_card(spec.get("verdict", "GO"))
        view.update(state=state, result=result if state == "succeeded" else None)
        return self._json(200, view)

    def _trace(self, run_id: str) -> httpx.Response:
        mode = self.runs.get(run_id, {}).get("spec", {}).get("trace", "ok")
        if mode == "missing":
            return self._json(404, {})
        if mode == "bad":
            return self._json(200, {"records": ["x"]})
        if mode == "down":
            return self._json(503, {})
        records = self.runs[run_id]["spec"].get("trace_records", good_trace())
        return self._json(200, records)
