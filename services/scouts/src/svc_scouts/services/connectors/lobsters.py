"""Lobsters public JSON (hottest, newest, per tag). Keyless; polite: a handful of pages a run."""

from datetime import UTC, datetime
from urllib.parse import quote

from pydantic import Field

from svc_scouts.services.context import Credentials, ScoutContext

from .base import Payload, as_list, parse_rows

BASE = "https://lobste.rs"


class Story(Payload):
    short_id: str
    short_id_url: str
    title: str
    url: str = ""
    description_plain: str | None = None
    description: str | None = None
    created_at: datetime
    score: int | None = None
    comment_count: int | None = None
    tags: list[str] = Field(default_factory=list)


class Lobsters:
    name = "lobsters"

    def credentials_ok(self, credentials: Credentials) -> bool:
        return True

    async def collect(self, ctx: ScoutContext) -> None:
        pages = int(ctx.options.get("pages", 3))
        paths = [f"/{feed}" for feed in ctx.options.get("feeds", ["hottest", "newest"])]
        paths += [f"/t/{quote(str(tag), safe='')}" for tag in ctx.options.get("tags", [])]
        newest_seen = str(ctx.cursor.get("newest_created_at", ""))
        newest_now = newest_seen
        for path in paths:
            for page in range(1, pages + 1):
                suffix = f"?page={page}" if page > 1 else ""
                rows = as_list(await ctx.get(f"{BASE}{path}.json{suffix}"), f"lobsters {path}")
                for story in parse_rows(Story, rows, ctx):
                    stamp = story.created_at.astimezone(UTC).isoformat()
                    newest_now = max(newest_now, stamp)
                    ctx.emit(
                        url=story.url or story.short_id_url,
                        title=story.title,
                        text=story.description_plain or story.description or "",
                        created_at=story.created_at,
                        score=max(story.score or 0, 0),
                        platform="lobsters",
                        metadata={
                            "tags": story.tags[:20],
                            "comments": story.comment_count or 0,
                            "discussion_url": story.short_id_url,
                        },
                    )
                # an empty page, or a page entirely older than the last run: stop paging
                if not rows or (newest_seen and _all_older(rows, newest_seen)):
                    break
        ctx.cursor["newest_created_at"] = newest_now


def _all_older(rows: list[object], stamp: str) -> bool:
    dated = [r for r in rows if isinstance(r, dict) and "created_at" in r]
    return bool(dated) and all(_older(r, stamp) for r in dated)


def _older(row: object, stamp: str) -> bool:
    created = row.get("created_at") if isinstance(row, dict) else None
    if not isinstance(created, str):
        return False
    try:
        moment = datetime.fromisoformat(created)
    except ValueError:
        return False
    return moment.astimezone(UTC) <= datetime.fromisoformat(stamp)
