from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Computed

from app.core.db.base import Base


class WorkflowQuestion(Base):
    __tablename__ = "workflow_questions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "question_id"),
        {"schema": "configuration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.workflow_definitions.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.questions.id", ondelete="RESTRICT"), nullable=False)
    is_required: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    visibility_condition: Mapped[Optional[dict]] = mapped_column(JSONB)
