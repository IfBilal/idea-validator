"""Stack Exchange API v2.3 search. Keyless (300 requests/day/IP); content is CC BY-SA 4.0."""

from urllib.parse import urlencode

from pydantic import Field

from svc_scouts.services.context import Credentials, ScoutContext
from svc_scouts.services.errors import FetchFailed, SourceRateLimited

from .base import Payload, as_dict, as_list, from_epoch, parse_rows

SEARCH = "https://api.stackexchange.com/2.3/search/advanced"
LICENCE = "CC BY-SA 4.0"


class Question(Payload):
    question_id: int
    title: str
    link: str
    body: str | None = None
    score: int | None = None
    creation_date: int
    tags: list[str] = Field(default_factory=list)
    answer_count: int | None = None
    view_count: int | None = None
    is_answered: bool | None = None


class StackExchange:
    name = "stackexchange"

    def credentials_ok(self, credentials: Credentials) -> bool:
        return True

    async def collect(self, ctx: ScoutContext) -> None:
        sites = [str(s) for s in ctx.options.get("sites", ["stackoverflow"])]
        queries = [str(q) for q in ctx.options.get("queries", [])]
        since = int(ctx.cursor.get("fromdate", 0))
        newest = since
        for site in sites:
            for q in queries:
                params = {
                    "order": "desc",
                    "sort": "votes",
                    "q": q,
                    "site": site,
                    "pagesize": 50,
                    "filter": "withbody",
                }
                if since:
                    params["fromdate"] = since
                body = as_dict(await ctx.get(f"{SEARCH}?{urlencode(params)}"), "stackexchange")
                if "error_id" in body:
                    raise FetchFailed(f"stackexchange error {body.get('error_id')}")
                rows = as_list(body.get("items", []), "stackexchange items")
                for question in parse_rows(Question, rows, ctx):
                    newest = max(newest, question.creation_date)
                    ctx.emit(
                        url=question.link,
                        title=question.title,
                        text=question.body or "",
                        created_at=from_epoch(question.creation_date),
                        score=max(question.score or 0, 0),
                        platform="stackexchange",
                        metadata={
                            "site": site,
                            "tags": question.tags[:10],
                            "answers": question.answer_count or 0,
                            "views": question.view_count or 0,
                            "answered": bool(question.is_answered),
                            "licence": LICENCE,
                            "query": q,
                        },
                    )
                if isinstance(body.get("backoff"), int) and body["backoff"] > 0:
                    # the API asks every client to pause: stop the run, never sleep through it
                    raise SourceRateLimited("stackexchange asked to back off", body["backoff"])
        ctx.cursor["fromdate"] = newest
