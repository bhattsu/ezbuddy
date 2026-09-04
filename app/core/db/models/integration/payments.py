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


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "fee_type IN ('COURT_FILING','PLATFORM_SERVICE','PAYMENT_PROCESSING','THIRD_PARTY','OTHER')",
            name="payments_fee_type_check",
        ),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETED','FAILED','REFUNDED','WAIVED')",
            name="payments_status_check",
        ),
        Index("idx_payments_session", "session_id"),
        Index("idx_payments_submission", "submission_id"),
        Index("idx_payments_provider", "provider_id"),
        Index("idx_payments_status", "status"),
        Index("idx_payments_fee_type", "fee_type"),
        {"schema": "integration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.filing_sessions.id", ondelete="CASCADE"), nullable=False)
    submission_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow.jurisdiction_submissions.id", ondelete="SET NULL"))
    provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("integration.api_providers.id"))
    fee_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default=sql_text("'COURT_FILING'"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=sql_text("'USD'"))
    payment_method: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=sql_text("'PENDING'"))
    external_transaction_id: Mapped[Optional[str]] = mapped_column(String(255))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
