from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models.documents.documents import Document
from app.core.db.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Document)

    async def list_by_case(self, case_id: UUID) -> list[Document]:
        result = await self.session.execute(select(Document).where(Document.case_id == case_id))
        return list(result.scalars().all())
