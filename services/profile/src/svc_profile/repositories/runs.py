import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import Approval, Run


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, run_id: str, lock: bool = False) -> Run | None:
        stmt = select(Run).where(Run.run_id == run_id)
        if lock:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add(self, run_id: str, fields: dict[str, Any]) -> Run:
        row = Run(run_id=run_id, **fields)
        self._session.add(row)
        await self._session.flush()
        return row

    async def touch(self, row: Run, fields: dict[str, Any]) -> Run:
        for name, value in fields.items():
            setattr(row, name, value)
        row.updated_at = func.now()
        await self._session.flush()
        await self._session.refresh(row)
        return row


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, fields: dict[str, Any]) -> Approval:
        row = Approval(**fields)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, approval_id: uuid.UUID, lock: bool = False) -> Approval | None:
        stmt = select(Approval).where(Approval.id == approval_id)
        if lock:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def decide(self, row: Approval, decision: str, decided_by: uuid.UUID | None) -> Approval:
        row.decision = decision
        row.decided_by = decided_by
        row.decided_at = func.now()
        await self._session.flush()
        await self._session.refresh(row)
        return row
