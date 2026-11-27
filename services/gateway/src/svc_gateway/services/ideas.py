"""Idea history and comparison. Ownership is M2's decision; a foreign idea is simply a 404."""

from common.errors import UpstreamError
from common.schemas import DIMENSIONS

from ..clients.ideas import IdeaClient
from ..clients.upstream import UNEXPECTED
from ..models.ideas import (
    CompareRow,
    Comparison,
    DimensionDelta,
    IdeaPage,
    VersionCard,
    VersionSummary,
)


def _delta(scores: dict[str, float | None]) -> float | None:
    known = [s for s in scores.values() if s is not None]
    return round(max(known) - min(known), 4) if len(known) >= 2 else None


class IdeaFlow:
    def __init__(self, ideas: IdeaClient) -> None:
        self._ideas = ideas

    async def page(self, user_id: str, limit: int, cursor: str | None) -> IdeaPage:
        return await self._ideas.page(user_id, limit, cursor)

    async def versions(self, user_id: str, idea_id: str) -> list[VersionSummary]:
        return await self._ideas.versions(idea_id, user_id)

    async def version(self, user_id: str, idea_id: str, version: int) -> VersionCard:
        return await self._ideas.detail(idea_id, user_id, version)

    async def compare(self, user_id: str, idea_ids: list[str]) -> Comparison:
        rows = {row.idea_id: row for row in await self._ideas.compare(user_id, idea_ids)}
        if set(rows) != set(idea_ids):
            raise UpstreamError(UNEXPECTED)
        ordered: list[CompareRow] = [rows[i] for i in idea_ids]
        names = list(DIMENSIONS)
        names += sorted({d for row in ordered for d in row.scores} - set(names))
        dimensions = []
        for name in names:
            scores = {row.idea_id: row.scores.get(name) for row in ordered}
            if any(s is not None for s in scores.values()):
                dimensions.append(
                    DimensionDelta(dimension=name, scores=scores, delta=_delta(scores))
                )
        return Comparison(
            ideas=ordered,
            dimensions=dimensions,
            verdicts={row.idea_id: row.verdict for row in ordered},
        )
