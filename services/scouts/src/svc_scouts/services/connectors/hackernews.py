"""Hacker News: Algolia search (Show/Ask HN, paged, resumable) plus the official Firebase feeds."""

from typing import Any
from urllib.parse import urlencode

from svc_scouts.services.context import Credentials, ScoutContext

from .base import Payload, as_dict, as_list, from_epoch, parse_rows

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
ITEM_URL = "https://news.ycombinator.com/item?id="
SEEN_CAP = 3000  # ids remembered per cursor so the Firebase feeds do not refetch old items


class AlgoliaHit(Payload):
    objectID: str
    title: str | None = None
    story_text: str | None = None
    url: str | None = None
    created_at_i: int
    points: int | None = None
    num_comments: int | None = None


class FirebaseItem(Payload):
    id: int
    type: str | None = None
    title: str | None = None
    text: str | None = None
    url: str | None = None
    time: int | None = None
    score: int | None = None
    descendants: int | None = None
    dead: bool = False
    deleted: bool = False


def _kind(title: str, tag: str | None) -> str:
    low = title.lower()
    if low.startswith("ask hn") or tag == "ask_hn":
        return "ask"
    if low.startswith("show hn") or tag == "show_hn":
        return "show"
    return "story"


class HackerNews:
    name = "hackernews"

    def credentials_ok(self, credentials: Credentials) -> bool:
        return True  # keyless

    async def collect(self, ctx: ScoutContext) -> None:
        for tag in ctx.options.get("algolia_tags", []):
            await self._algolia(ctx, str(tag))
        for feed in ctx.options.get("feeds", []):
            await self._firebase(ctx, str(feed))

    async def _algolia(self, ctx: ScoutContext, tag: str) -> None:
        since = int(ctx.cursor.get("algolia_since", {}).get(tag, 0))
        newest = since
        page = 0
        max_pages = int(ctx.options.get("algolia_max_pages", 10))
        while page < max_pages:
            query = urlencode(
                {
                    "tags": tag,
                    "hitsPerPage": 100,
                    "page": page,
                    "numericFilters": f"created_at_i>{since}",
                }
            )
            body = as_dict(await ctx.get(f"{ALGOLIA}?{query}"), "algolia")
            hits = as_list(body.get("hits", []), "algolia hits")
            for hit in parse_rows(AlgoliaHit, hits, ctx):
                newest = max(newest, hit.created_at_i)
                title = hit.title or ""
                ctx.emit(
                    url=hit.url or f"{ITEM_URL}{hit.objectID}",
                    title=title,
                    text=hit.story_text or "",
                    created_at=from_epoch(hit.created_at_i),
                    score=max(hit.points or 0, 0),
                    platform="hackernews",
                    metadata={
                        "hn_id": hit.objectID,
                        "kind": _kind(title, tag),
                        "comments": hit.num_comments or 0,
                        "discussion_url": f"{ITEM_URL}{hit.objectID}",
                    },
                )
            page += 1
            if not hits or page >= int(body.get("nbPages", 0) or 0):
                break
        else:
            return  # stopped on the page cap: the cursor stays so the rest is read next run
        # only reached when the feed was read to its end: safe to move the resume point
        ctx.cursor.setdefault("algolia_since", {})[tag] = newest

    async def _firebase(self, ctx: ScoutContext, feed: str) -> None:
        ids = as_list(await ctx.get(f"{FIREBASE}/{feed}.json"), f"firebase {feed}")
        seen: list[int] = list(ctx.cursor.get("firebase_seen", {}).get(feed, []))
        seen_set = set(seen)
        take = int(ctx.options.get("firebase_max_items", 100))
        fresh = [i for i in ids if isinstance(i, int) and i not in seen_set][:take]
        done: list[int] = []
        for item_id in fresh:
            raw: Any = await ctx.get(f"{FIREBASE}/item/{item_id}.json")
            done.append(item_id)
            if raw is None:  # deleted or not yet visible
                ctx.dropped += 1
                continue
            for item in parse_rows(FirebaseItem, [raw], ctx):
                if item.dead or item.deleted or not item.title:
                    ctx.dropped += 1
                    continue
                ctx.emit(
                    url=item.url or f"{ITEM_URL}{item.id}",
                    title=item.title,
                    text=item.text or "",
                    created_at=from_epoch(item.time),
                    score=max(item.score or 0, 0),
                    platform="hackernews",
                    metadata={
                        "hn_id": str(item.id),
                        "kind": _kind(item.title, None),
                        "comments": item.descendants or 0,
                        "discussion_url": f"{ITEM_URL}{item.id}",
                    },
                )
        merged = (seen + done)[-SEEN_CAP:]
        ctx.cursor.setdefault("firebase_seen", {})[feed] = merged
