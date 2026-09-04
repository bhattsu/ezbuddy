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


class Jurisdiction(Base):
    __tablename__ = "jurisdictions"
    __table_args__ = (
        CheckConstraint(
            "jurisdiction_type IN ('DISTRICT_COURT','FAMILY_COURT','SMALL_CLAIMS_COURT',"
            "'PROBATE_COURT','COUNTY_COURT','MUNICIPAL_COURT','APPELLATE_COURT','OTHER')",
            name="jurisdictions_jurisdiction_type_check",
        ),
        {"schema": "configuration"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()"))
    county_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("configuration.counties.id", ondelete="RESTRICT"), nullable=False)
    jurisdiction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP"))
