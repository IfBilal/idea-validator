"""In-memory ``NicheStore`` (the service recomputes on start, so nothing needs to survive)."""

from ..models.domain import Snapshot


class InMemoryNicheStore:
    def __init__(self) -> None:
        self._snapshot: Snapshot | None = None

    async def save(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot

    async def load(self) -> Snapshot | None:
        return self._snapshot
