"""Product Hunt GraphQL API v2 (developer token). Off until the token exists AND M5 can POST."""

import json
from datetime import datetime
from typing import Any

from pydantic import Field

from svc_scouts.models.runs import ApiRequest
from svc_scouts.services.context import Credentials, ScoutContext
from svc_scouts.services.errors import FetchFailed

from .base import Payload, as_dict, as_list, parse_rows

ENDPOINT = "https://api.producthunt.com/v2/api/graphql"
QUERY = (
    "query($after: String, $postedAfter: DateTime) {"
    " posts(first: 50, after: $after, order: NEWEST, postedAfter: $postedAfter) {"
    " pageInfo { endCursor hasNextPage }"
    " edges { node { id name tagline description url votesCount createdAt"
    " topics(first: 8) { edges { node { name } } } } } } }"
)


class Node(Payload):
    id: str
    name: str
    tagline: str | None = None
    description: str | None = None
    url: str
    votesCount: int | None = None
    createdAt: datetime | None = None
    topics: dict[str, Any] = Field(default_factory=dict)


def _topics(node: Node) -> list[str]:
    edges = node.topics.get("edges", [])
    names = [e.get("node", {}).get("name") for e in edges if isinstance(e, dict)]
    return [n for n in names if isinstance(n, str)][:8]


class ProductHunt:
    name = "producthunt"

    def credentials_ok(self, credentials: Credentials) -> bool:
        return bool(credentials.producthunt_token)

    async def collect(self, ctx: ScoutContext) -> None:
        token = ctx.credentials.producthunt_token
        if token is None:
            raise FetchFailed("producthunt token is not configured")
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        posted_after = ctx.cursor.get("posted_after")
        newest = str(posted_after or "")
        after: str | None = None
        for _ in range(int(ctx.options.get("pages", 5))):
            payload = json.dumps(
                {"query": QUERY, "variables": {"after": after, "postedAfter": posted_after}}
            )
            body = as_dict(
                await ctx.call(
                    ApiRequest(
                        url=ENDPOINT,
                        method="POST",
                        headers=headers,
                        body=payload,
                        auth_required=True,
                    )
                ),
                "producthunt",
            )
            if body.get("errors"):
                raise FetchFailed("producthunt returned GraphQL errors")
            posts = as_dict(as_dict(body.get("data", {}), "ph data").get("posts", {}), "ph posts")
            edges = as_list(posts.get("edges", []), "ph edges")
            nodes = [e.get("node") if isinstance(e, dict) else None for e in edges]
            for node in parse_rows(Node, nodes, ctx):
                if node.createdAt:
                    newest = max(newest, node.createdAt.isoformat())
                ctx.emit(
                    url=node.url,
                    title=node.name,
                    text=" ".join(p for p in (node.tagline, node.description) if p),
                    created_at=node.createdAt,
                    score=max(node.votesCount or 0, 0),
                    platform="producthunt",
                    metadata={"topics": _topics(node), "ph_id": node.id},
                )
            info = as_dict(posts.get("pageInfo", {}), "ph pageInfo")
            after = info.get("endCursor") if info.get("hasNextPage") else None
            if after is None:
                break
        else:
            return  # page cap reached: keep the cursor so the rest is read next run
        if newest:
            ctx.cursor["posted_after"] = newest
