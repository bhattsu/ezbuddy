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


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        Index(
            "idx_workflow_def_active_unique",
            "jurisdiction_id",
            "case_subtype_id",
            unique=True,
            postgresql_where=sql_text("status = 'ACTIVE'"),
        ),
        Index("idx_workflow_def_code", "workflow_code"),
        {"schema": "configuration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.jurisdictions.id", ondelete="RESTRICT"), nullable=False)
    case_subtype_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_subtypes.id", ondelete="RESTRICT"), nullable=False)
    workflow_code: Mapped[Optional[str]] = mapped_column(String(100))
    workflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    status: Mapped[Optional[str]] = mapped_column(String(20), server_default=sql_text("'ACTIVE'"))
    excel_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
