"""Storing a verdict produced by M11: the full payload plus one row per dimension score."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.errors import NotFound, ValidationFailed

from ..models.storage import StoreVerdict, VerdictStored
from ..repositories.db import transaction
from ..repositories.ideas import IdeaRepository
from ..repositories.tables import DimensionScoreRow, VerdictRow


class VerdictService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def store(self, body: StoreVerdict) -> VerdictStored:
        v = body.verdict
        if v.idea_id != str(body.idea_id) or v.version != body.version:
            raise ValidationFailed("verdict idea_id/version do not match the request")
        async with transaction(self._sessions) as session:
            repo = IdeaRepository(session)
            if await repo.owned(body.idea_id, body.user_id) is None:
                raise NotFound("idea not found", {"idea_id": str(body.idea_id)})
            if await repo.version(body.idea_id, body.version) is None:
                raise NotFound("idea version not found", {"version": body.version})
            row = VerdictRow(
                idea_id=body.idea_id,
                version=body.version,
                verdict=v.verdict,
                score=v.score,
                confidence=v.confidence,
                run_id=v.run_id,
                stability=v.stability,
                profile_version=body.profile_version,
                payload=v.model_dump(mode="json", by_alias=True, exclude_unset=True),
                engine_version=v.versions.engine,
                rubrics_version=v.versions.rubrics,
                weights_version=v.versions.profile_weights,
            )
            scores = [
                DimensionScoreRow(
                    dimension=s.dimension,
                    score=s.score,
                    conf=s.confidence,
                    evidence=list(s.evidence_ids),
                    features=s.features,
                    reason=s.reason,
                    rubric_version=s.rubric_version,
                    model=s.model,
                    adjusted_by_challenge=s.adjusted_by_challenge or 0,
                )
                for s in v.scores
            ]
            stored = await repo.add_verdict(row, scores)
            return VerdictStored(verdict_id=str(stored.id))
