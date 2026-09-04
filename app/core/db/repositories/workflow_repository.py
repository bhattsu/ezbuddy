from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models.workflow.filing_answers import FilingAnswer
from app.core.db.models.workflow.filing_sessions import FilingSession
from app.core.db.repositories.base import BaseRepository


class WorkflowRepository(BaseRepository[FilingSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, FilingSession)

    async def get_with_answers(self, session_id: UUID) -> FilingSession | None:
        return await self.get_by_id(session_id)

    async def list_answers(self, session_id: UUID) -> list[FilingAnswer]:
        result = await self.session.execute(
            select(FilingAnswer).where(FilingAnswer.session_id == session_id)
        )
        return list(result.scalars().all())

    async def upsert_answer(
        self, *, session_id: UUID, question_id: UUID, answer_value: str | None
    ) -> FilingAnswer:
        answer = (
            await self.session.execute(
                select(FilingAnswer).where(
                    FilingAnswer.session_id == session_id,
                    FilingAnswer.question_id == question_id,
                )
            )
        ).scalar_one_or_none()
        if answer is None:
            answer = FilingAnswer(session_id=session_id, question_id=question_id, answer_value=answer_value)
            self.session.add(answer)
        else:
            answer.answer_value = answer_value
        await self.session.flush()
        await self.session.refresh(answer)
        return answer
