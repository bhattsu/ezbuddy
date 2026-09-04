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


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','ON_HOLD','CLOSED','DISMISSED')"),
        CheckConstraint("priority IN ('LOW','MEDIUM','HIGH','URGENT')"),
        Index("idx_cases_user", "user_id"),
        Index("idx_cases_status", "status"),
        Index("idx_cases_case_number", "case_number", unique=True, postgresql_where=sql_text("case_number IS NOT NULL")),
        {"schema": "operational"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operational.users.id"), nullable=False)
    case_number: Mapped[Optional[str]] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    state_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.states.id"), nullable=False)
    county_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.counties.id"), nullable=False)
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.jurisdictions.id"), nullable=False)
    case_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_types.id"), nullable=False)
    case_subtype_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.case_subtypes.id"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=sql_text("'OPEN'"))
    priority: Mapped[Optional[str]] = mapped_column(String(20))
    filing_deadline: Mapped[Optional[date]] = mapped_column(Date)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=sql_text("CURRENT_TIMESTAMP"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
