"""What a connector sees during one run: budgeted fetching, defensive emitting, the cursor."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import SecretStr, ValidationError

from svc_scouts.models.items import RawItem
from svc_scouts.models.runs import ApiRequest
from svc_scouts.models.sources import SourceCfg

from .errors import BudgetExhausted, FetchFailed, MalformedPayload, SourceRateLimited
from .ports import Fetcher


@dataclass(frozen=True)
class Credentials:
    """Optional secrets. ``repr`` hides them; they are never logged or stored."""

    github_token: SecretStr | None = field(default=None, repr=False)
    reddit_client_id: SecretStr | None = field(default=None, repr=False)
    reddit_client_secret: SecretStr | None = field(default=None, repr=False)
    producthunt_token: SecretStr | None = field(default=None, repr=False)


class ScoutContext:
    """One run of one source. Connectors only talk to the world through this object."""

    def __init__(
        self,
        *,
        source: str,
        cfg: SourceCfg,
        fetcher: Fetcher,
        credentials: Credentials,
        cursor: dict[str, Any],
        run_id: str,
        limit: int,
        now: datetime,
    ) -> None:
        self.source = source
        self.cfg = cfg
        self.credentials = credentials
        self.cursor = cursor  # connectors mutate it only when progress is safe to resume from
        self.run_id = run_id
        self.now = now
        self.limit = min(limit, cfg.max_items_per_run)
        self.items: list[RawItem] = []
        self.dropped = 0
        self.requests = 0
        self.flagged = 0
        self._fetcher = fetcher
        self._seen: set[str] = set()

    @property
    def options(self) -> dict[str, Any]:
        return self.cfg.options

    async def get(self, url: str) -> Any:
        return await self.call(ApiRequest(url=url))

    async def call(self, request: ApiRequest) -> Any:
        """One budgeted call through M5; returns the parsed JSON body."""
        if self.requests >= self.cfg.max_requests_per_run:
            raise BudgetExhausted("request budget used up")
        self.requests += 1
        page = await self._fetcher.fetch(request, source=self.source, run_id=self.run_id)
        if page.flagged:
            self.flagged += 1
        if page.status == 429 or page.status == 403:
            raise SourceRateLimited(f"{self.source} answered HTTP {page.status}")
        if page.status >= 400:
            raise FetchFailed(f"{self.source} answered HTTP {page.status}", page.status)
        if page.json_ is not None:
            return page.json_
        if page.text:
            try:
                return json.loads(page.text)
            except (ValueError, RecursionError) as exc:
                raise MalformedPayload(f"{self.source} body is not JSON") from exc
        raise MalformedPayload(f"{self.source} body is empty")

    def seen(self, url: str) -> bool:
        return url in self._seen

    def emit(self, **fields: Any) -> bool:
        """Validate and keep one item. A malformed item is dropped and counted, never raised.

        Raises ``BudgetExhausted`` right after the item that reaches the run's item limit.
        """
        try:
            item = RawItem(source=self.source, **fields)
        except (ValidationError, TypeError, ValueError, RecursionError):
            self.dropped += 1
            return False
        if item.url in self._seen:
            return False
        self._seen.add(item.url)
        self.items.append(item)
        if len(self.items) >= self.limit:
            raise BudgetExhausted("item limit reached")
        return True
