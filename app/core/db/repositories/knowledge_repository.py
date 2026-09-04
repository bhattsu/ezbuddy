from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models.knowledge.kb_vectors import KbVector
from app.core.db.repositories.base import BaseRepository


class KnowledgeRepository(BaseRepository[KbVector]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, KbVector)

    async def list_by_matter(self, matter_type: str, *, limit: int = 50) -> list[KbVector]:
        result = await self.session.execute(
            select(KbVector).where(KbVector.matter_type == matter_type).limit(limit)
        )
        return list(result.scalars().all())
