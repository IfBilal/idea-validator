"""Typed client for M2's idea, version, compare and run endpoints (``/internal/*``).

Every read carries the ``user_id`` from the caller's token, and M2 answers 404 for anything that
belongs to someone else. The gateway never decides ownership by itself.
"""

from typing import Any

from common.http import ServiceClient

from ..models.ideas import (
    CompareRow,
    IdeaCreated,
    IdeaPage,
    RunRecord,
    VersionCard,
    VersionSummary,
)
from .upstream import guarded, parse, parse_list


class IdeaClient:
    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    async def create_idea(self, user_id: str, title: str, text: str) -> IdeaCreated:
        body = {"user_id": user_id, "title": title, "text": text, "source": "own"}
        data = await guarded(lambda: self._client.post_json("/internal/ideas", body), "idea")
        return parse(IdeaCreated, data)

    async def add_version(self, idea_id: str, user_id: str, text: str) -> IdeaCreated:
        body = {"user_id": user_id, "text": text}
        path = f"/internal/ideas/{idea_id}/versions"
        data = await guarded(lambda: self._client.post_json(path, body), "idea")
        return parse(IdeaCreated, data)

    async def page(self, user_id: str, limit: int, cursor: str | None) -> IdeaPage:
        params: dict[str, Any] = {"user_id": user_id, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        data = await guarded(
            lambda: self._client.get_json("/internal/ideas", params=params), "idea"
        )
        return parse(IdeaPage, data)

    async def versions(self, idea_id: str, user_id: str) -> list[VersionSummary]:
        path = f"/internal/ideas/{idea_id}/versions"
        params = {"user_id": user_id}
        data = await guarded(lambda: self._client.get_json(path, params=params), "idea")
        return parse_list(VersionSummary, data)

    async def detail(self, idea_id: str, user_id: str, version: int) -> VersionCard:
        path = f"/internal/ideas/{idea_id}/versions/{version}"
        params = {"user_id": user_id}
        data = await guarded(lambda: self._client.get_json(path, params=params), "idea version")
        if isinstance(data, dict) and "verdict_payload" in data:
            data = {**data, "card": data["verdict_payload"]}
        return parse(VersionCard, data)

    async def compare(self, user_id: str, idea_ids: list[str]) -> list[CompareRow]:
        params: dict[str, Any] = {"user_id": user_id, "idea_ids": idea_ids}
        data = await guarded(
            lambda: self._client.get_json("/internal/compare", params=params), "idea"
        )
        return parse_list(CompareRow, data)

    async def put_run(
        self, run_id: str, user_id: str, kind: str, state: str, run_input: dict[str, Any]
    ) -> None:
        body = {"user_id": user_id, "kind": kind, "state": state, "input": run_input}
        path = f"/internal/runs/{run_id}"
        await guarded(lambda: self._client.request("PUT", path, json=body), "run")

    async def get_run(self, run_id: str, user_id: str) -> RunRecord:
        path = f"/internal/runs/{run_id}"
        params = {"user_id": user_id}
        data = await guarded(lambda: self._client.get_json(path, params=params), "run")
        return parse(RunRecord, data)
