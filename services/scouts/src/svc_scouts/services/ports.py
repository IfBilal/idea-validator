"""What the scout service needs from the outside world, as ports (Postgres/M5 or fakes)."""

from collections.abc import Sequence
from typing import Protocol

from svc_scouts.models.items import RawItem
from svc_scouts.models.runs import ApiRequest, Page, ScoutState


class Fetcher(Protocol):
    """The only way to reach the internet: M5 POST /fetch."""

    async def fetch(self, request: ApiRequest, *, source: str, run_id: str) -> Page: ...


class RawStore(Protocol):
    async def upsert_items(self, items: Sequence[RawItem]) -> tuple[int, int, int]:
        """Insert or refresh by (source, url): returns (added, updated, unchanged).

        A row whose text changed goes back to ``processed = false`` so ingestion sees it again.
        """
        ...

    async def get_state(self, source: str) -> ScoutState: ...

    async def save_state(self, source: str, state: ScoutState) -> None: ...

    async def ping(self) -> bool: ...
