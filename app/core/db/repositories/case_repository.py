from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models.operational.cases import Case
from app.core.db.repositories.base import BaseRepository


class CaseRepository(BaseRepository[Case]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Case)

    async def list_by_user(self, user_id: UUID, *, limit: int = 100) -> list[Case]:
        result = await self.session.execute(
            select(Case).where(Case.user_id == user_id, Case.deleted_at.is_(None)).limit(limit)
        )
        return list(result.scalars().all())
