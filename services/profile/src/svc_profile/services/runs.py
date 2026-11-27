"""Run state (persisted so a run can resume after an interrupt) and approval requests."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.errors import Conflict, NotFound

from ..models.storage import ApprovalDecision, ApprovalOut, NewApproval, RunBody, RunOut
from ..repositories.db import transaction
from ..repositories.runs import ApprovalRepository, RunRepository
from ..repositories.tables import Approval, Run
from ..repositories.users import UserRepository


def _run(row: Run) -> RunOut:
    return RunOut(
        run_id=row.run_id,
        user_id=row.user_id,
        kind=row.kind,
        state=row.state,
        input=row.input,
        context=row.context,
        pending_question=row.pending_question,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _approval(row: Approval) -> ApprovalOut:
    def num(value: object) -> float | None:
        return None if value is None else float(value)  # type: ignore[arg-type]

    return ApprovalOut(
        id=str(row.id),
        run_id=row.run_id,
        action=row.action,
        decision=row.decision,
        estimated_cost_usd=num(row.estimated_cost_usd),
        budget_usd=num(row.budget_usd),
        spent_usd=num(row.spent_usd),
        requested_at=row.requested_at,
        decided_at=row.decided_at,
    )


class RunService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, run_id: str, user_id: uuid.UUID) -> RunOut:
        async with self._sessions() as session:
            row = await RunRepository(session).get(run_id)
        if row is None or row.user_id != user_id:
            raise NotFound("run not found", {"run_id": run_id})
        return _run(row)

    async def put(self, run_id: str, body: RunBody) -> RunOut:
        """Create or update a run. A run belongs to one user for its whole life."""
        async with transaction(self._sessions) as session:
            if await UserRepository(session).lock(body.user_id) is None:
                raise NotFound("user not found", {"user_id": str(body.user_id)})
            repo = RunRepository(session)
            existing = await repo.get(run_id, lock=True)
            fields = body.model_dump()
            if existing is None:
                return _run(await repo.add(run_id, fields))
            if existing.user_id != body.user_id:
                raise NotFound("run not found", {"run_id": run_id})
            fields.pop("user_id")
            return _run(await repo.touch(existing, fields))


class ApprovalService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, body: NewApproval) -> ApprovalOut:
        async with transaction(self._sessions) as session:
            return _approval(await ApprovalRepository(session).add(body.model_dump()))

    async def decide(self, approval_id: uuid.UUID, body: ApprovalDecision) -> ApprovalOut:
        async with transaction(self._sessions) as session:
            repo = ApprovalRepository(session)
            row = await repo.get(approval_id, lock=True)
            if row is None:
                raise NotFound("approval not found", {"id": str(approval_id)})
            if row.decision != "pending":
                raise Conflict("approval was already decided", {"decision": row.decision})
            return _approval(await repo.decide(row, body.decision, body.decided_by))
