"""GitHub REST search by topic. Keyless; a token only raises GitHub's own limit (never logged)."""

from datetime import datetime
from urllib.parse import urlencode

from pydantic import Field

from svc_scouts.models.runs import ApiRequest
from svc_scouts.services.context import Credentials, ScoutContext

from .base import Payload, as_dict, as_list, parse_rows

SEARCH = "https://api.github.com/search/repositories"


class Repo(Payload):
    full_name: str
    html_url: str
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    stargazers_count: int
    forks_count: int | None = None
    language: str | None = None
    created_at: datetime | None = None
    pushed_at: datetime | None = None
    fork: bool = False
    archived: bool = False


class GitHub:
    name = "github"

    def credentials_ok(self, credentials: Credentials) -> bool:
        return True  # the token is optional

    async def collect(self, ctx: ScoutContext) -> None:
        min_stars = int(ctx.options.get("min_stars", 50))
        pages = int(ctx.options.get("pages_per_topic", 1))
        pushed_since = ctx.cursor.get("pushed_since")
        for topic in ctx.options.get("topics", []):
            qualifiers = f"topic:{topic} stars:>={min_stars}"
            if pushed_since:
                qualifiers += f" pushed:>={pushed_since}"
            for page in range(1, pages + 1):
                query = urlencode(
                    {
                        "q": qualifiers,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": 100,
                        "page": page,
                    }
                )
                body = as_dict(await ctx.call(self._request(ctx, f"{SEARCH}?{query}")), "github")
                rows = as_list(body.get("items", []), "github items")
                for repo in parse_rows(Repo, rows, ctx):
                    if repo.fork:
                        continue
                    ctx.emit(
                        url=repo.html_url,
                        title=repo.full_name,
                        text=" ".join(
                            part for part in (repo.description, " ".join(repo.topics)) if part
                        ),
                        created_at=repo.created_at,
                        score=repo.stargazers_count,
                        platform="github",
                        metadata={
                            "stars": repo.stargazers_count,
                            "forks": repo.forks_count or 0,
                            "topics": repo.topics[:30],
                            "language": repo.language,
                            "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
                            "archived": repo.archived,
                            "search_topic": topic,
                        },
                    )
                if len(rows) < 100:
                    break
        # every topic was read: next run only needs repositories pushed since today
        ctx.cursor["pushed_since"] = ctx.now.date().isoformat()

    @staticmethod
    def _request(ctx: ScoutContext, url: str) -> ApiRequest:
        token = ctx.credentials.github_token
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
        return ApiRequest(url=url, headers=headers)  # headers are dropped if M5 cannot forward
