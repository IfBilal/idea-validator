"""Shared test helpers: fake M5/M6/M7 transports and sample data."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from common.http import ServiceClient
from common.schemas import EvidenceItem, StudentProfile
from svc_ideation.clients.analyst import AnalystClient
from svc_ideation.clients.kb import KnowledgeBaseClient
from svc_ideation.clients.llm import LlmClient

REPO_ROOT = Path(__file__).resolve().parents[4]
PROMPTS_DIR = REPO_ROOT / "prompts"


def eid(card: int, chunk: int = 1) -> str:
    return f"card_{card:08x}#c{chunk}"


def evidence(
    card: int = 1,
    chunk: int = 1,
    quote: str = "users complain export is slow",
    name: str | None = "NoteApp",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid(card, chunk),  # type: ignore[arg-type]
        card_id=f"card_{card:08x}",  # type: ignore[arg-type]
        quote=quote,
        source="hackernews",
        url="https://news.ycombinator.com/item?id=1",
        retrieved_at=datetime(2026, 3, 2, tzinfo=UTC),
        card_name=name,
    )


def evidence_json(item: EvidenceItem) -> dict[str, Any]:
    return item.model_dump(mode="json")


def profile(
    hours: int = 10, weeks: int = 10, team: int = 1, skills: list[str] | None = None
) -> StudentProfile:
    return StudentProfile(
        user_id="u1",
        skills=skills or ["python"],
        hours_per_week=hours,
        weeks_available=weeks,
        team_size=team,
        compute="laptop",
        goal="portfolio",
        interests=["education"],
    )


def raw_idea(**over: Any) -> dict[str, Any]:
    idea: dict[str, Any] = {
        "title": "Study Planner Lite",
        "summary": "Plans revision around exam dates for university students.",
        "target_user": "university students",
        "core_features": ["exam countdown", "spaced repetition", "weekly plan"],
        "gap_ref": "gap_1",
        "gap_evidence_ids": [eid(1)],
        "fit_reason": "Fits a python student with ten weeks.",
        "est_scope": "S",
    }
    idea.update(over)
    return idea


def llm_reply(content: str | dict[str, Any]) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "content": text,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "latency_ms": 10,
        "provider": "fake",
        "model": "fake-1",
    }


Handler = Callable[[httpx.Request], httpx.Response]


def make_service_client(
    handler: Handler,
    token: str | None = "test-token",  # noqa: S107
) -> ServiceClient:
    return ServiceClient(
        "http://svc.test",
        timeout_s=1,
        max_retries=0,
        internal_token=token,
        transport=httpx.MockTransport(handler),
    )


class FakeLlm:
    """Scripted M5: returns queued replies in order (the last one repeats); records requests."""

    def __init__(self, *replies: str | dict[str, Any] | int) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self._last: str | dict[str, Any] | int = "{}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/llm/v1/chat/completions"
        self.requests.append(json.loads(request.content))
        if self.replies:
            self._last = self.replies.pop(0)
        if isinstance(self._last, int):
            return httpx.Response(self._last, json={"error": {"code": "UPSTREAM_ERROR"}})
        return httpx.Response(200, json=llm_reply(self._last))

    def client(self) -> LlmClient:
        return LlmClient(make_service_client(self.handler))


class FakeKb:
    """Scripted M6: ``search`` results via a callable, evidence existence by the ``known`` set."""

    def __init__(
        self,
        known: list[EvidenceItem] | None = None,
        search: Callable[[dict[str, Any]], list[EvidenceItem]] | None = None,
    ) -> None:
        self.known = {item.evidence_id: item for item in (known or [])}
        self.search_fn = search or (lambda _body: [])
        self.searches: list[dict[str, Any]] = []
        self.batches: list[list[str]] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(503, json={})
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/search":
            self.searches.append(body)
            items = [evidence_json(i) for i in self.search_fn(body)]
            return httpx.Response(200, json={"items": items, "index_version": 1})
        if request.url.path == "/evidence/batch":
            self.batches.append(body["ids"])
            found = [evidence_json(self.known[i]) for i in body["ids"] if i in self.known]
            missing = [i for i in body["ids"] if i not in self.known]
            return httpx.Response(200, json={"items": found, "missing": missing})
        if request.url.path.startswith("/evidence/"):
            key = request.url.raw_path.decode().removeprefix("/evidence/").replace("%23", "#")
            if key in self.known:
                return httpx.Response(200, json=evidence_json(self.known[key]))
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        return httpx.Response(404, json={})

    def client(self) -> KnowledgeBaseClient:
        return KnowledgeBaseClient(make_service_client(self.handler))


class FakeAnalyst:
    def __init__(self, gaps: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.gaps = gaps or {}
        self.fail = False
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(503, json={})
        assert request.url.path == "/gaps"
        niche = request.url.params["niche"]
        self.calls.append(niche)
        return httpx.Response(200, json={"gaps": self.gaps.get(niche, [])})

    def client(self) -> AnalystClient:
        return AnalystClient(make_service_client(self.handler))
