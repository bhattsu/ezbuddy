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


class FilingSession(Base):
    __tablename__ = "filing_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','SUBMITTED','REJECTED','CANCELLED')"),
        Index("idx_fs_case", "case_id"),
        Index("idx_fs_workflow", "workflow_id"),
        Index("idx_fs_user", "user_id"),
        Index("idx_fs_status", "status"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.cases.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.workflow_definitions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=sql_text("'IN_PROGRESS'"))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    current_step: Mapped[Optional[str]] = mapped_column(String(100))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
