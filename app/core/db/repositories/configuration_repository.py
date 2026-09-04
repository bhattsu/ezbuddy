from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models.configuration.questions import Question
from app.core.db.models.configuration.states import State
from app.core.db.models.configuration.workflow_definitions import WorkflowDefinition
from app.core.db.repositories.base import BaseRepository


class ConfigurationRepository(BaseRepository[State]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, State)

    async def get_state_by_code(self, code: str) -> State | None:
        return (await self.session.execute(select(State).where(State.code == code))).scalar_one_or_none()

    async def get_question_by_code(self, code: str) -> Question | None:
        return (await self.session.execute(select(Question).where(Question.code == code))).scalar_one_or_none()

    async def get_active_workflow(
        self, jurisdiction_id: UUID, case_subtype_id: UUID
    ) -> WorkflowDefinition | None:
        return (
            await self.session.execute(
                select(WorkflowDefinition)
                .where(
                    WorkflowDefinition.jurisdiction_id == jurisdiction_id,
                    WorkflowDefinition.case_subtype_id == case_subtype_id,
                    WorkflowDefinition.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
