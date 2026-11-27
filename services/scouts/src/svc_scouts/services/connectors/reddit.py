"""Reddit Data API (OAuth, official). Off until credentials exist AND M5 can forward auth.

Reddit forbids training models on its data, so every item is marked ``training_allowed: false``
and ``retrieval_only`` for downstream (docs/data-sources.md). We keep no usernames.
"""

import base64
from typing import Any
from urllib.parse import urlencode

from svc_scouts.models.runs import ApiRequest
from svc_scouts.services.context import Credentials, ScoutContext
from svc_scouts.services.errors import FetchFailed

from .base import Payload, as_dict, as_list, from_epoch, parse_rows

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"  # noqa: S105 (a public url)
API = "https://oauth.reddit.com"
PERMALINK = "https://www.reddit.com"


class Post(Payload):
    id: str
    title: str
    selftext: str = ""
    permalink: str
    created_utc: float
    score: int | None = None
    subreddit: str | None = None
    num_comments: int | None = None
    over_18: bool = False
    stickied: bool = False


class Reddit:
    name = "reddit"

    def credentials_ok(self, credentials: Credentials) -> bool:
        return bool(credentials.reddit_client_id and credentials.reddit_client_secret)

    async def collect(self, ctx: ScoutContext) -> None:
        token = await self._token(ctx)
        headers = {"Authorization": f"Bearer {token}"}
        period = str(ctx.options.get("period", "month"))
        for sub in ctx.options.get("subreddits", []):
            query = urlencode({"t": period, "limit": 100, "raw_json": 1})
            url = f"{API}/r/{sub}/top?{query}"
            body = as_dict(
                await ctx.call(ApiRequest(url=url, headers=headers, auth_required=True)), "reddit"
            )
            data = as_dict(body.get("data", {}), "reddit data")
            children = as_list(data.get("children", []), "reddit children")
            posts = [c.get("data") if isinstance(c, dict) else None for c in children]
            for post in parse_rows(Post, posts, ctx):
                if post.over_18 or post.stickied:
                    continue
                text = "" if post.selftext in ("[removed]", "[deleted]") else post.selftext
                ctx.emit(
                    url=f"{PERMALINK}{post.permalink}",
                    title=post.title,
                    text=text,
                    created_at=from_epoch(post.created_utc),
                    score=max(post.score or 0, 0),
                    platform="reddit",
                    metadata={
                        "subreddit": post.subreddit or sub,
                        "comments": post.num_comments or 0,
                        "training_allowed": False,
                        "retrieval_only": True,
                    },
                )

    async def _token(self, ctx: ScoutContext) -> str:
        creds = ctx.credentials
        if not (creds.reddit_client_id and creds.reddit_client_secret):
            raise FetchFailed("reddit credentials are not configured")
        basic = base64.b64encode(
            f"{creds.reddit_client_id.get_secret_value()}:"
            f"{creds.reddit_client_secret.get_secret_value()}".encode()
        ).decode()
        body: Any = await ctx.call(
            ApiRequest(
                url=TOKEN_URL,
                method="POST",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body="grant_type=client_credentials",
                auth_required=True,
            )
        )
        token = as_dict(body, "reddit token").get("access_token")
        if not isinstance(token, str) or not token:
            raise FetchFailed("reddit token endpoint gave no access_token")
        return token
