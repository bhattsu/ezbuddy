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


class JurisdictionSubmission(Base):
    __tablename__ = "jurisdiction_submissions"
    __table_args__ = (
        CheckConstraint(
            "submission_status IN ('PENDING','PROCESSING','ACCEPTED','REJECTED','UNDER_REVIEW','CANCELLED')"
        ),
        Index("idx_submission_session", "session_id"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("integration.api_providers.id"), nullable=False)
    reference_number: Mapped[Optional[str]] = mapped_column(String(100))
    submission_status: Mapped[str] = mapped_column(String(30), nullable=False)
    response_message: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    response_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
